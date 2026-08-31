"""Daily-run orchestration: seed / list / full pipeline.

Builds the LLM and Telegram clients once from the config and threads them into
the per-job stages. The seen-jobs state machine (first-run sentinel, staged
save points) is preserved exactly from the original pipeline.
"""
import concurrent.futures
import datetime
import html
import sys
import os
from dataclasses import dataclass, field

from ..composition import ConfigurationError, load_components
from ..components import (
    CVArtifact,
    DefaultCVRenderer,
    DefaultJobEvaluator,
    DefaultPromptSet,
    DigestOutcome,
    default_components,
)
from ..config import (
    BASE_TEX_FILE,
    CRITERIA_FILE,
    CV_TAILORING_PROMPT_FILE,
    SEEN_JOBS_FILE,
    load_base_tex,
    load_criteria,
    load_tailoring_instructions,
)
from ..digest import DeferredEntry, DigestContext, FitEntry, ReviewEntry
from ..identity import job_identity_keys, normalize_url
from ..llm.clients import LLMClient, model_shutdown_warning
from ..llm.eval import evaluate_job
from ..llm.summarize import summarize_job
from ..models import coerce_job
from ..notify.telegram import TelegramClient
from ..sources.fetch import fetch_jobs_with_health, select_sources
from ..sources.health import format_source_health
from ..state.git_sync import pull_state, push_state
from ..state.seen_jobs import (
    SeenSet,
    acknowledge_block_alert,
    delivery_retry_state,
    evaluation_signature,
    load_seen_jobs,
    mark_delivery_notified,
    pending_block_alerts,
    record_delivery_failure,
    record_evaluation,
    save_seen_jobs,
    should_reevaluate,
    state_size_summary,
)
from .stages import (
    CVDeliveryError,
    DeliveryOutcome,
    _format_deferred_notification,
    _format_notification,
    _format_uncertain_notification,
    clean_job_description,
    ensure_job_description,
)


@dataclass
class RunStats:
    new_jobs: int = 0
    evaluated: int = 0
    non_fit: int = 0
    fits: int = 0
    uncertain: int = 0
    deferred: int = 0
    evaluation_failed: int = 0
    preparation_failed: int = 0
    notification_sent: int = 0
    cv_sent: int = 0
    delivery_failed: int = 0
    retry_attempts: int = 0
    retries_waiting: int = 0
    known_fit_retries: int = 0
    newly_blocked: int = 0
    failure_details: list = field(default_factory=list)


def _format_run_summary(
    stats: RunStats, source_warning="", cv_required=True,
    telegram_markup=True,
) -> str:
    lines = [
        (
            "✅ <b>Job search complete</b>"
            if telegram_markup
            else "✅ Job search complete"
        ),
        f"New candidates: {stats.new_jobs}",
        f"Evaluated: {stats.evaluated} (non-fit: {stats.non_fit}, fit: {stats.fits})",
        f"Needs review (uncertain): {stats.uncertain}",
        f"Deferred: {stats.deferred}",
        f"Fit notifications sent: {stats.notification_sent}",
        (
            f"Verified CVs delivered: {stats.cv_sent}"
            if cv_required
            else "CV delivery: disabled"
        ),
        f"Evaluation failures: {stats.evaluation_failed}",
        f"Preparation failures: {stats.preparation_failed}",
        f"Delivery failures: {stats.delivery_failed}",
        f"Retry attempts executed: {stats.retry_attempts}",
        f"Retries waiting for backoff: {stats.retries_waiting}",
        f"Known-fit retries that skipped evaluation: {stats.known_fit_retries}",
        f"Newly blocked fits: {stats.newly_blocked}",
    ]
    if stats.fits == 0 and stats.retries_waiting == 0:
        lines.extend(("", "No evaluated jobs matched your criteria."))
    if cv_required and stats.fits > stats.cv_sent:
        pending = stats.fits - stats.cv_sent
        noun = "fit" if pending == 1 else "fits"
        lines.extend(
            (
                "",
                f"⚠️ {pending} {noun} not fully delivered; they will retry according to scheduled backoff or remain blocked as shown below.",
            )
        )
    if stats.failure_details:
        lines.extend((
            "",
            (
                "<b>Fit delivery failures</b>"
                if telegram_markup
                else "Fit delivery failures"
            ),
        ))
        lines.extend(
            detail if telegram_markup else html.unescape(detail)
            for detail in stats.failure_details[:10]
        )
    if source_warning:
        lines.extend((
            "",
            (
                "⚠️ <b>Source health</b>"
                if telegram_markup
                else "⚠️ Source health"
            ),
            html.escape(source_warning) if telegram_markup else str(source_warning),
        ))
    return "\n".join(lines)


def _today():
    return datetime.date.today()


def _bounded(value, limit=180):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _failure_detail(job, stage, state):
    label = " — ".join(
        part for part in (_bounded(job.get("title")), _bounded(job.get("company"))) if part
    ) or "Unknown job"
    status = "blocked; use /tailor" if state.blocked else f"next retry {state.retry_on.isoformat()}"
    return "• {}: {}, attempt {}, {}".format(
        html.escape(label),
        html.escape(stage.capitalize()),
        state.attempt,
        status,
    )


def _pending_fit_message(job, evaluation, retry_on):
    title = html.escape(_bounded(job.get("title") or "Unknown Title"))
    company = html.escape(_bounded(job.get("company") or "Unknown Company"))
    reason = html.escape(_bounded(evaluation.get("reason"), 600))
    url = str(job.get("url") or "").strip()
    link = f'<a href="{html.escape(url, quote=True)}">View posting</a>' if url else ""
    return "\n".join(
        part
        for part in (
            "⏳ <b>Fit found; verified CV pending</b>",
            f"<b>{title}</b>",
            f"<b>{company}</b>",
            link,
            "",
            f"<i>{reason}</i>",
            f"Automated retry: {retry_on.isoformat()}",
        )
        if part or part == ""
    )


def _block_alert_message(payload):
    title = html.escape(_bounded(payload.get("title") or "Unknown job"))
    company = html.escape(_bounded(payload.get("company")))
    stage = html.escape(_bounded(payload.get("stage"), 40))
    url = str(payload.get("url") or "").strip()
    lines = [
        "🛑 <b>automated CV delivery blocked</b>",
        f"{title}" + (f" — {company}" if company else ""),
        f"Failed stage: {stage}",
        "Three automated attempts were exhausted. Use /tailor to retry manually.",
    ]
    if url:
        lines.append(f'<a href="{html.escape(url, quote=True)}">View posting</a>')
    return "\n".join(lines)


def _seen_file(cfg):
    return getattr(cfg, "seen_jobs_file", SEEN_JOBS_FILE)


def _load_seen_for(cfg):
    path = _seen_file(cfg)
    return load_seen_jobs() if path == SEEN_JOBS_FILE else load_seen_jobs(path)


def _save_seen_for(cfg, seen):
    path = _seen_file(cfg)
    if path == SEEN_JOBS_FILE:
        return save_seen_jobs(seen)
    return save_seen_jobs(seen, path)


def _load_file_for(cfg, attribute, default, loader):
    path = getattr(cfg, attribute, default)
    return loader() if path == default else loader(path)


def _drain_block_alerts(seen, telegram, cfg=None):
    for token, payload in pending_block_alerts(seen):
        try:
            telegram.send_message(_block_alert_message(payload))
        except Exception as exc:
            print(f"Telegram blocked-fit alert error: {exc}", file=sys.stderr)
            continue
        acknowledge_block_alert(seen, token)
        save_seen_jobs(seen) if cfg is None else _save_seen_for(cfg, seen)


def _record_fit_failure(seen, stats, job, stage, today, telegram, cfg=None):
    previous = delivery_retry_state(seen, **job)
    state = record_delivery_failure(seen, job, today, stage)
    save_seen_jobs(seen) if cfg is None else _save_seen_for(cfg, seen)
    if len(stats.failure_details) < 10:
        stats.failure_details.append(_failure_detail(job, stage, state))
    if state.blocked and not previous.blocked:
        stats.newly_blocked += 1
        _drain_block_alerts(seen, telegram, cfg)
    return state


class _PipelineDefaultEvaluator(DefaultJobEvaluator):
    """Default evaluator seam that preserves monkeypatch-compatible wrappers."""

    def evaluate(self, llm, criteria, job):
        if getattr(self.prompts, "revision", "") == DefaultPromptSet.revision:
            return evaluate_job(llm, criteria, job)
        return evaluate_job(llm, criteria, job, prompts=self.prompts)


def _runtime_components(cfg, command):
    prompts = DefaultPromptSet()
    evaluator = _PipelineDefaultEvaluator(prompts)
    try:
        defaults = default_components(
            cfg,
            prompts=prompts,
            llm=LLMClient.from_config(cfg),
            evaluator=evaluator,
            telegram=TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id),
        )
    except ConfigurationError:
        raise
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "Built-in components could not be constructed ({}: {})".format(
                type(exc).__name__, exc
            )
        ) from exc
    configured = load_components(cfg, command=command, defaults=defaults)
    # Replacing only prompts is the common override. Keep the default evaluator
    # but bind it to the configured prompts so the revision and prompt body both
    # take effect; an explicitly replaced evaluator is left untouched.
    if configured.evaluator is evaluator and configured.prompts is not prompts:
        configured.evaluator = _PipelineDefaultEvaluator(configured.prompts)
    return configured


def _evaluate_candidate(llm, criteria, job, evaluator=None):
    """Evaluate a candidate only when its cleaned description is sufficient."""
    if not ensure_job_description(job):
        return None
    return (
        evaluator.evaluate(llm, criteria, job)
        if evaluator is not None
        else evaluate_job(llm, criteria, job)
    )


def _prepare_with_renderer(renderer, llm, job, evaluation=None):
    artifact = renderer.render_tailored(llm, job, evaluation)
    job = coerce_job(job)
    payload = {
        "title": job.get("title", "?"),
        "company": job.get("company", "?"),
        "artifact": artifact,
        # Compatibility fields keep legacy digest/backends working while the
        # generic artifact is threaded end-to-end.
        "pdf_bytes": artifact.content,
        "cv_filename": artifact.filename,
        "media_type": artifact.media_type,
    }
    if evaluation is not None:
        payload["message"] = _format_notification(job, evaluation)
    return payload


class _OutputNoticeAdapter:
    """Expose the legacy send_message seam over a configured output pair."""

    def __init__(self, renderer, backend):
        self.renderer = renderer
        self.backend = backend

    def send_message(self, message):
        return self.send_notice(message)

    def send_notice(self, notice, **context):
        rendered = self.renderer.render_notice(notice, **context)
        return self.backend.deliver_notice(rendered)

    def send_error(self, exc):
        return self.send_notice(
            "{}: {}".format(type(exc).__name__, exc),
            level="error",
            title="Pipeline error",
            icon="⚠️",
            code=True,
        )


# Reserved for pipeline metadata; these entries must never act as seen identities.
_DEFERRED_MARKER_PREFIX = "deferred:"


def _deferred_markers(job) -> set[str]:
    markers = set()
    url_key = normalize_url(job.url)
    for key in job_identity_keys(job):
        kind = "url" if url_key and key == url_key else "job"
        markers.add(f"{_DEFERRED_MARKER_PREFIX}{kind}:{key}")
    return markers


def _fetch_for_pipeline(cfg):
    kwargs = {
        "source_names": select_sources(cfg.sources_enable, cfg.sources_disable),
        "verbose": True,
    }
    if _seen_file(cfg) != SEEN_JOBS_FILE:
        kwargs["seen_jobs_file"] = _seen_file(cfg)
    report = fetch_jobs_with_health(**kwargs)
    summary = format_source_health(report)
    if summary:
        print(summary, file=sys.stderr)
    return report


def run_seed(cfg) -> int:
    """Mark all currently fetched jobs as seen without evaluating."""
    _runtime_components(cfg, command="seed")
    report = _fetch_for_pipeline(cfg)
    if not report.has_usable_source:
        print("No usable job source completed; seed aborted.", file=sys.stderr)
        return 1
    raw_jobs = report.jobs
    seen_raw = _load_seen_for(cfg)
    seen = seen_raw if seen_raw is not None else SeenSet()
    added = 0
    for j in raw_jobs:
        keys = job_identity_keys(j)
        url_key = normalize_url(j.url)
        if url_key and url_key not in seen:
            added += 1
        seen.update(keys)
    _save_seen_for(cfg, seen)
    print(f"Seed complete — {len(raw_jobs)} job(s) marked seen ({added} new URL entries added).")
    return 0


def run_list(cfg) -> int:
    """Fetch and print new jobs (not in seen_jobs.json) without AI/Telegram."""
    _runtime_components(cfg, command="list")
    report = _fetch_for_pipeline(cfg)
    if not report.has_usable_source:
        print("No usable job source completed; list aborted.", file=sys.stderr)
        return 1
    raw_jobs = report.jobs
    seen_raw = _load_seen_for(cfg)
    seen = seen_raw if seen_raw is not None else SeenSet()
    new_jobs = [j for j in raw_jobs if seen.isdisjoint(job_identity_keys(j))]
    print(f"{len(new_jobs)} new job(s):\n")
    for j in new_jobs:
        date_str = j.date_posted.strftime("%Y-%m-%d") if j.date_posted else "n/a"
        print(f"  {j.title}")
        print(f"  {j.company} | {j.location or 'n/a'} | {j.source} | {date_str}")
        print(f"  {j.url}")
        print()
    return 0


def _summaries(llm, jobs, workers, prompts=None):
    """One short LLM summary per job, computed concurrently (aligned with jobs)."""
    if not jobs:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        if prompts is None or getattr(prompts, "revision", "") == DefaultPromptSet.revision:
            return list(pool.map(lambda job: summarize_job(llm, job), jobs))
        return list(pool.map(lambda job: summarize_job(llm, job, prompts=prompts), jobs))


def _unique_artifact(artifact, taken):
    if artifact.filename not in taken:
        return artifact
    stem, extension = os.path.splitext(artifact.filename)
    counter = 2
    name = "{}_{}{}".format(stem, counter, extension)
    while name in taken:
        counter += 1
        name = "{}_{}{}".format(stem, counter, extension)
    return CVArtifact(name, artifact.media_type, artifact.content)


def _commit_deferrals(seen, deferrals, today, cfg=None) -> None:
    """Record queued deferrals: markers to suppress repeat notices, plus the
    signature that stops a reopened job from re-deferring every run."""
    for markers, job, signature in deferrals:
        seen.update(markers)
        if signature is not None:
            record_evaluation(seen, job, signature, "deferred", today)
    if deferrals:
        save_seen_jobs(seen) if cfg is None else _save_seen_for(cfg, seen)


def _digest_context(
    components, cfg, seen, stats, today, prepared, prepared_reviews,
    uncertain, newly_deferred, source_warning, notifier=None,
):
    fit_jobs = [job for job, _payload, _retry, _evaluation in prepared]
    review_jobs = [job for job, _evaluation in uncertain]
    summaries = _summaries(
        components.llm,
        fit_jobs + review_jobs,
        cfg.eval_workers,
        components.prompts,
    )
    fit_summaries = summaries[:len(fit_jobs)]
    review_summaries = summaries[len(fit_jobs):]
    taken = set()
    fits = []
    for (job, payload, _retry, evaluation), summary in zip(prepared, fit_summaries):
        artifact = payload.get("artifact")
        if artifact is not None:
            artifact = _unique_artifact(artifact, taken)
            taken.add(artifact.filename)
        fits.append(FitEntry(job, evaluation, summary, artifact=artifact))
    review_payloads = {id(job): payload for job, payload, _evaluation in prepared_reviews}
    review = []
    for (job, evaluation), summary in zip(uncertain, review_summaries):
        payload = review_payloads.get(id(job), {})
        artifact = payload.get("artifact")
        if artifact is not None:
            artifact = _unique_artifact(artifact, taken)
            taken.add(artifact.filename)
        review.append(ReviewEntry(job, evaluation, summary, artifact=artifact))
    sections, sections_error = components.section_provider.load()
    if sections_error:
        # Announced here rather than at load time so a config problem surfaces
        # only on a run that actually had something to group, and exactly once
        # regardless of which backend the digest goes out through.
        print("  Digest sections: {}".format(sections_error), file=sys.stderr)
        if notifier is not None:
            try:
                notifier.send_message(
                    "⚠️ Digest sections: {}".format(html.escape(sections_error))
                )
            except Exception as exc:
                print(f"Sections alert error: {exc}", file=sys.stderr)
    return DigestContext(
        date=today,
        stats=stats,
        source_warning=source_warning,
        usage_summary=" ".join(
            (components.llm.usage_summary(), state_size_summary(seen, _seen_file(cfg)))
        ),
        fits=fits,
        review=review,
        deferred=[DeferredEntry(job=job) for job in newly_deferred],
        sections=sections,
        sections_error=sections_error,
    )


def _deliver_digest(
    components, cfg, seen, stats, today, prepared, prepared_reviews,
    uncertain, newly_deferred, source_warning, signature_for, deferrals,
):
    notifier = _OutputNoticeAdapter(
        components.output_renderer, components.output_backend
    )
    telegram_markup = components.output_renderer.kind == "telegram"
    cv_required = components.output_backend.cv_mode == "required"
    ctx = _digest_context(
        components, cfg, seen, stats, today, prepared, prepared_reviews,
        uncertain, newly_deferred, source_warning, notifier,
    )
    if not (ctx.fits or ctx.review or ctx.deferred):
        _commit_deferrals(seen, deferrals, today, cfg)
        try:
            notifier.send_message(
                _format_run_summary(
                    stats,
                    source_warning,
                    cv_required=cv_required,
                    telegram_markup=telegram_markup,
                )
            )
        except Exception as exc:
            print("Output summary error: {}".format(exc), file=sys.stderr)
            return False
        return True

    artifacts = tuple(
        entry.artifact
        for entry in list(ctx.fits) + list(ctx.review)
        if entry.artifact is not None
    )
    fit_artifact_count = sum(
        payload.get("artifact") is not None
        for _job, payload, _retry, _evaluation in prepared
    )
    try:
        rendered = components.output_renderer.render_digest(ctx)
    except Exception as exc:
        # A renderer that blows up is a presentation problem for this run's
        # content: the fits stay unseen and the next run tries again.
        outcome = DigestOutcome(False, error=exc)
    else:
        # A backend reports *delivery* problems as an outcome. Anything it lets
        # escape is a bug (a broken bundler, say); that stays fatal rather than
        # spending every job's retry budget on something retrying cannot fix.
        outcome = components.output_backend.deliver_digest(
            rendered, artifacts, context=ctx, date=today
        )
    cv_complete = not cv_required or outcome.cv_sent >= len(artifacts)
    complete = outcome.delivered and outcome.notification_sent and cv_complete
    if not complete:
        stats.notification_sent += sum(
            1
            for _job, _payload, retry_state, _evaluation in prepared
            if outcome.notification_sent
            and not (retry_state and retry_state.notified)
        )
        stats.cv_sent += min(max(outcome.cv_sent, 0), fit_artifact_count)
        # Mark every prepared fit notified even when the digest itself failed:
        # the fallback run summary below names each pending fit, so the user has
        # heard about them, and the retry can take the known-fit route instead
        # of re-paying fact extraction, bullet selection and pdflatex for a job
        # already known to be a fit (finding N9).
        for job, _payload, _retry, _evaluation in prepared:
            mark_delivery_notified(seen, **job)
        if prepared:
            _save_seen_for(cfg, seen)

        if outcome.error:
            detail = str(outcome.error)
        elif not outcome.notification_sent:
            detail = "notification was not sent"
        elif not outcome.delivered:
            detail = "backend did not confirm atomic delivery"
        else:
            detail = "only {} of {} required CV artifacts were delivered".format(
                outcome.cv_sent, len(artifacts)
            )
        print("Configured digest delivery failed: {}".format(detail), file=sys.stderr)

        for job, _payload, _retry, _evaluation in prepared:
            stats.delivery_failed += 1
            _record_fit_failure(
                seen, stats, job, "delivery", today, notifier, cfg
            )
        try:
            notifier.send_message(
                _format_run_summary(
                    stats,
                    source_warning,
                    cv_required=cv_required,
                    telegram_markup=telegram_markup,
                )
            )
        except Exception as exc:
            print("Output summary error: {}".format(exc), file=sys.stderr)
        return False

    for job, payload, retry_state, _evaluation in prepared:
        stats.notification_sent += int(not (retry_state and retry_state.notified))
        mark_delivery_notified(seen, **job)
        seen.update(job_identity_keys(job))
        signature = signature_for(job)
        if signature is not None:
            record_evaluation(seen, job, signature, "fit", today)
    if prepared:
        _save_seen_for(cfg, seen)

    for job, _evaluation in uncertain:
        # Required-CV backends only complete review entries whose artifact was
        # produced; text-only backends complete the rendered review directly.
        has_artifact = any(entry.job is job and entry.artifact for entry in ctx.review)
        if cv_required and not has_artifact:
            continue
        seen.update(job_identity_keys(job))
        signature = signature_for(job)
        if signature is not None:
            record_evaluation(seen, job, signature, "uncertain", today)
    if uncertain:
        _save_seen_for(cfg, seen)
    stats.cv_sent += min(max(outcome.cv_sent, 0), fit_artifact_count)
    _commit_deferrals(seen, deferrals, today, cfg)
    return True


def run_daily(cfg, test: bool = False) -> int:
    """The full scheduled pipeline: fetch → evaluate → tailor → deliver."""
    components = _runtime_components(cfg, command="daily")
    llm = components.llm
    notifier = _OutputNoticeAdapter(
        components.output_renderer, components.output_backend
    )
    state_mutation_allowed = False
    exit_code = 0
    try:
        shutdown_note = model_shutdown_warning(cfg.llm_primary_model)
        if shutdown_note:
            print(shutdown_note, flush=True)
        if not cfg.llm_fallback_api_key and getattr(
            cfg, "llm_fallback_auth_mode", "bearer"
        ) != "none":
            print(
                "Note: no LLM fallback configured (LLM_FALLBACK_API_KEY / OPENAI_API_KEY unset).",
                flush=True,
            )
            if shutdown_note:
                # Without a fallback, a retired primary is a total outage: every
                # job fails evaluation and the run delivers nothing.
                print(
                    "  ⚠️ With no fallback, a retired primary model means the run "
                    "delivers nothing at all — set LLM_FALLBACK_API_KEY / OPENAI_API_KEY.",
                    flush=True,
                )
        criteria = (
            _load_file_for(cfg, "criteria_file", CRITERIA_FILE, load_criteria)
            if getattr(components.evaluator, "requires_criteria", True)
            else ""
        )
        crit_ver = components.evaluator.fingerprint(criteria)
        tailoring_instructions = ""
        base_tex = ""
        # Preflight every file owned by the built-in renderer before state sync
        # or fetch. A whole custom renderer owns its inputs, while
        # cv_mode=disabled must not require CV source/compatibility files.
        if (
            components.output_backend.cv_mode == "required"
            and isinstance(getattr(components, "cv_renderer", None), DefaultCVRenderer)
        ):
            tailoring_instructions = _load_file_for(
                cfg,
                "cv_tailoring_prompt_file",
                CV_TAILORING_PROMPT_FILE,
                load_tailoring_instructions,
            )
            profile_base_path = components.cv_renderer.profile.base_tex_path
            base_tex = (
                load_base_tex()
                if profile_base_path == BASE_TEX_FILE
                else load_base_tex(profile_base_path)
            )

        if cfg.state_sync:
            # Pull only after configuration and required local files have
            # passed preflight. linkedin-guest then sees the shared baseline
            # during fetch. Sync remains best-effort and never raises.
            path = _seen_file(cfg)
            pull_state() if path == SEEN_JOBS_FILE else pull_state(seen_file=path)

        print("Fetching jobs...", flush=True)
        report = _fetch_for_pipeline(cfg)
        if not report.has_usable_source:
            message = "No selected source completed successfully; evaluation and state updates were aborted."
            print("No usable job source completed; daily run aborted.", file=sys.stderr)
            try:
                notifier.send_notice(
                    message,
                    level="error",
                    title="Job source outage",
                    icon="🚨",
                )
            except Exception as exc:
                print(f"Output source-outage notification error: {exc}", file=sys.stderr)
            return 1
        state_mutation_allowed = True
        raw_jobs = report.jobs
        source_warning = format_source_health(report, unhealthy_only=True)

        if test:
            if not raw_jobs:
                print("No jobs found — nothing to test.")
                return 0
            j = raw_jobs[0]
            d = coerce_job(j)
            print("Test mode: processing one job without touching seen_jobs.json.")
            if not ensure_job_description(d):
                print(
                    "Test job deferred — insufficient job-description text.",
                    flush=True,
                )
                try:
                    notifier.send_message(_format_deferred_notification([d]))
                except Exception as exc:
                    print(
                        f"Telegram deferred-job notification error: {exc}",
                        file=sys.stderr,
                    )
                print("Done.", flush=True)
                return 0
            if not components.candidate_filter.include(d):
                print("Test job excluded by the configured candidate filter.")
                print("Done.", flush=True)
                return 0
            evaluation = components.evaluator.evaluate(llm, criteria, d)
            if not evaluation.get("fit"):
                print("    Skip — {}".format(evaluation.get("reason", "")))
                print("Done.", flush=True)
                return 0
            artifact = None
            if components.output_backend.cv_mode == "required":
                artifact = components.cv_renderer.render_tailored(
                    llm, d, evaluation
                )
            rendered = components.output_renderer.render_fit(d, evaluation)
            outcome = components.output_backend.deliver_fit(
                rendered, artifact, job=d
            )
            if not outcome.complete:
                raise CVDeliveryError(outcome)
            print("Done.", flush=True)
            return 0

        seen_raw = _load_seen_for(cfg)
        first_run = seen_raw is None
        seen = seen_raw if seen_raw is not None else SeenSet()
        # Reported, never pruned: the eval:* markers are order 4d's reopen
        # history. Logging the growth every run means a future pruning decision
        # rests on real numbers.
        print(state_size_summary(seen, _seen_file(cfg)), flush=True)
        _drain_block_alerts(seen, notifier, cfg)

        today = _today()
        cutoff = today - datetime.timedelta(days=7)

        stats = RunStats()
        # evaluation_jobs: list of (Job, retry_state)
        # Keys are NOT added to seen yet — added only after successful processing.
        evaluation_jobs = []
        fits = []
        uncertain = []
        newly_deferred = []
        # (markers, job, signature) triples awaiting the digest send; empty on the
        # legacy path, which commits them immediately (see _register_deferral).
        pending_deferrals = []
        candidate_count = 0
        # Content/criteria signature per identity, captured from the RAW scraped
        # description before ensure_job_description mutates it, so the same value
        # drives both the reopen check and the later verdict record.
        job_signatures = {}

        def _signature_for(job):
            keys = job_identity_keys(job)
            return job_signatures.get(keys[0]) if keys else None

        def _register_deferral(job, signature=None):
            """Record one insufficient-description deferral.

            The deferral markers suppress repeat notices, so committing them
            before the notice is delivered would silence a job the user never
            heard about. On the digest path they therefore wait for a successful
            ZIP send (finding N9); the legacy path sends its own message right
            after this loop, so it commits immediately as before.

            ``signature`` is only meaningful for a *reopened* (already-seen) job,
            where a deferred verdict stops the reopen→defer loop next run. For a
            brand-new deferral it is inert and would grow state every run.
            """
            stats.deferred += 1
            markers = _deferred_markers(job)
            if not markers or markers.isdisjoint(seen):
                newly_deferred.append(job)
            reopened = not seen.isdisjoint(job_identity_keys(job))
            signature = signature if reopened else None
            if cfg.digest_delivery:
                pending_deferrals.append((markers, job, signature))
                return
            seen.update(markers)
            if signature is not None:
                record_evaluation(seen, job, signature, "deferred", today)

        for value in raw_jobs:
            job = coerce_job(value)
            if not components.candidate_filter.include(job):
                continue
            keys = job_identity_keys(job)
            signature = evaluation_signature(clean_job_description(job.description), crit_ver)
            if keys:
                job_signatures[keys[0]] = signature
            if not seen.isdisjoint(keys):
                # Known identity: skip unless a structured lifecycle record shows
                # the content or criteria signature changed (a reopen). Legacy
                # string-only seen entries have no record and stay suppressed.
                if not should_reevaluate(seen, job, signature):
                    continue
            if first_run:
                # On first run, silently mark jobs older than 7 days as seen
                # without evaluating. Job normalizes date_posted to a date (or
                # None) at construction, so no isinstance dance is needed here.
                posted = job.date_posted
                if posted is not None and posted < cutoff:
                    seen.update(keys)
                    continue
            retry_state = delivery_retry_state(seen, **job)
            if retry_state.blocked:
                continue
            candidate_count += 1
            if retry_state.attempt == 0 and not retry_state.notified:
                stats.new_jobs += 1
            if retry_state.retry_on and retry_state.retry_on > today:
                stats.retries_waiting += 1
                continue
            if retry_state.notified:
                if not ensure_job_description(job):
                    _register_deferral(job)
                    continue
                stats.retry_attempts += int(retry_state.attempt > 0)
                stats.known_fit_retries += 1
                stats.fits += 1
                fits.append((job, None, retry_state))
                continue
            evaluation_jobs.append((job, retry_state))

        # Persist seen set now — captures first-run silenced jobs; new jobs are NOT yet included.
        _save_seen_for(cfg, seen)
        print(f"Found {candidate_count} new or retryable job(s).", flush=True)

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # LLM calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        if evaluation_jobs:
            print(f"Evaluating {len(evaluation_jobs)} job(s) with {cfg.eval_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.eval_workers) as pool:
                future_to_job = {
                    pool.submit(
                        _evaluate_candidate, llm, criteria, job, components.evaluator
                    ): (job, retry_state)
                    for job, retry_state in evaluation_jobs
                }
                for future in concurrent.futures.as_completed(future_to_job):
                    job, retry_state = future_to_job[future]
                    try:
                        evaluation = future.result()
                    except Exception as exc:
                        # Evaluation failed — leave unseen so it retries next run.
                        stats.evaluation_failed += 1
                        print(
                            f"  Error evaluating '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    if evaluation is None:
                        _register_deferral(job, _signature_for(job))
                        continue
                    stats.retry_attempts += int(retry_state.attempt > 0)
                    stats.evaluated += 1
                    if evaluation.get("fit"):
                        stats.fits += 1
                        fits.append((job, evaluation, retry_state))
                    elif evaluation.get("verdict") == "uncertain":
                        # Policy could not confidently decide: surface for review
                        # rather than discard. Marked seen so it notifies once;
                        # the structured lifecycle reopens it if content/criteria
                        # later change. In digest mode the whole review section
                        # rides on the single ZIP send, so marking is deferred to
                        # _deliver_digest and only applied once delivery succeeds
                        # (otherwise a failed send would bury it forever).
                        stats.uncertain += 1
                        print(f"    Review '{job.get('title')}' — {evaluation.get('reason', '')}")
                        uncertain.append((job, evaluation))
                        if not cfg.digest_delivery:
                            seen.update(job_identity_keys(job))
                            signature = _signature_for(job)
                            if signature is not None:
                                record_evaluation(seen, job, signature, "uncertain", today)
                    else:
                        # Not a fit: mark seen so it won't be reprocessed.
                        stats.non_fit += 1
                        print(f"    Skip '{job.get('title')}' — {evaluation.get('reason', '')}")
                        seen.update(job_identity_keys(job))
                        signature = _signature_for(job)
                        if signature is not None:
                            record_evaluation(seen, job, signature, "nonfit", today)
        if stats.deferred:
            print(
                f"Deferred {stats.deferred} job(s) with insufficient description text.",
                flush=True,
            )
        # In digest mode these are folded into the HTML dashboard instead of
        # going out as their own Telegram messages.
        if not cfg.digest_delivery:
            if newly_deferred:
                try:
                    notifier.send_message(_format_deferred_notification(newly_deferred))
                except Exception as exc:
                    print(
                        f"Telegram deferred-job notification error: {exc}",
                        file=sys.stderr,
                    )
            if uncertain:
                try:
                    notifier.send_message(_format_uncertain_notification(uncertain))
                except Exception as exc:
                    print(
                        f"Telegram uncertain-job notification error: {exc}",
                        file=sys.stderr,
                    )
        # Persist the non-fits and uncertain jobs captured above in one write.
        _save_seen_for(cfg, seen)
        cv_required = components.output_backend.cv_mode == "required"
        review_to_tailor = uncertain if cfg.digest_delivery and cv_required else []
        print(
            "{} fit(s) and {} review job(s) to tailor.".format(
                len(fits), len(review_to_tailor)
            ),
            flush=True,
        )

        # ── Stage 3: Tailor + compile actionable jobs concurrently ───────────
        # Tailoring is LLM-bound and compilation is CPU-bound; a smaller pool
        # keeps parallel pdflatex runs from starving the runner. No Telegram I/O
        # happens here, so order doesn't matter and failures stay soft.
        prepared = []  # list of (job, payload, retry_state, evaluation) ready to send
        prepared_reviews = []  # list of (job, payload, evaluation) ready to bundle
        if fits and not cv_required:
            for job, evaluation, retry_state in fits:
                prepared.append(
                    (
                        job,
                        {
                            "title": job.get("title", "?"),
                            "company": job.get("company", "?"),
                            "message": _format_notification(job, evaluation or {}),
                        },
                        retry_state,
                        evaluation,
                    )
                )
        elif fits or review_to_tailor:
            print(
                "Tailoring {} CV(s) with {} workers...".format(
                    len(fits) + len(review_to_tailor), cfg.tailor_workers
                ),
                flush=True,
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.tailor_workers) as pool:
                future_to_candidate = {}
                for job, evaluation, retry_state in fits:
                    # A retried fit was announced in an earlier run, so it gets
                    # no second fit message — passing no evaluation is what
                    # tells the renderer to skip it.
                    future = pool.submit(
                        _prepare_with_renderer,
                        components.cv_renderer,
                        llm,
                        job,
                        None if retry_state.notified else evaluation,
                    )
                    future_to_candidate[future] = ("fit", job, evaluation, retry_state)
                for job, evaluation in review_to_tailor:
                    future = pool.submit(
                        _prepare_with_renderer,
                        components.cv_renderer,
                        llm,
                        job,
                        evaluation,
                    )
                    future_to_candidate[future] = ("review", job, evaluation, None)
                for future in concurrent.futures.as_completed(future_to_candidate):
                    kind, job, evaluation, retry_state = future_to_candidate[future]
                    try:
                        payload = future.result()
                    except Exception as exc:
                        stats.preparation_failed += 1
                        if kind == "review":
                            print(
                                "  Error preparing review CV for '{}'; it will retry next run: {}".format(
                                    job.get("title"), exc
                                ),
                                file=sys.stderr,
                            )
                            continue
                        failure_state = _record_fit_failure(
                            seen,
                            stats,
                            job,
                            "preparation",
                            today,
                            notifier,
                            cfg,
                        )
                        print(
                            f"  Error preparing '{job.get('title')}' — {failure_state}: {exc}",
                            file=sys.stderr,
                        )
                        if failure_state.attempt == 1 and not retry_state.notified:
                            try:
                                notifier.send_message(
                                    _pending_fit_message(job, evaluation or {}, failure_state.retry_on)
                                )
                            except Exception as notify_exc:
                                print(
                                    f"  Pending-fit notification failed for '{job.get('title')}': {notify_exc}",
                                    file=sys.stderr,
                                )
                            else:
                                stats.notification_sent += 1
                                mark_delivery_notified(seen, **job)
                                _save_seen_for(cfg, seen)
                        continue
                    if kind == "review":
                        prepared_reviews.append((job, payload, evaluation))
                    else:
                        prepared.append((job, payload, retry_state, evaluation))

        # ── Stage 4: Deliver ─────────────────────────────────────────────────
        if cfg.digest_delivery:
            # One digest per run: the rendered dashboard plus every tailored CV,
            # folding in the uncertain and deferred jobs too. All state
            # reconciliation (mark seen on success, record a delivery failure on
            # send error) happens inside _deliver_digest.
            if not _deliver_digest(
                components,
                cfg,
                seen,
                stats,
                today,
                prepared,
                prepared_reviews,
                uncertain,
                newly_deferred,
                source_warning,
                _signature_for,
                pending_deferrals,
            ):
                exit_code = 1
        else:
            # Per-job path (DIGEST_DELIVERY=0): one rendered fit and its CV per
            # job, sequentially, then a text run summary.
            for job, payload, retry_state, evaluation in prepared:
                try:
                    rendered = components.output_renderer.render_fit(
                        job, evaluation or {"reason": "Previously matched."}
                    )
                    outcome = components.output_backend.deliver_fit(
                        rendered,
                        payload.get("artifact"),
                        notification_already_sent=retry_state.notified,
                        job=job,
                    )
                except Exception as exc:
                    outcome = DeliveryOutcome(
                        error=exc,
                        notification_satisfied=retry_state.notified,
                        cv_required=components.output_backend.cv_mode == "required",
                    )
                stats.notification_sent += int(outcome.notification_sent)
                stats.cv_sent += int(outcome.cv_sent)
                if outcome.notification_satisfied:
                    # Recorded before any failure below, because
                    # _record_fit_failure is what persists the seen set on the
                    # error path — a delivered notification must not be re-sent
                    # next run just because the document failed.
                    mark_delivery_notified(seen, **job)
                if outcome.complete:
                    seen.update(job_identity_keys(job))
                    signature = _signature_for(job)
                    if signature is not None:
                        record_evaluation(seen, job, signature, "fit", today)
                    _save_seen_for(cfg, seen)
                else:
                    stats.delivery_failed += 1
                    stage = "document" if outcome.notification_satisfied else "notification"
                    _record_fit_failure(seen, stats, job, stage, today, notifier, cfg)
                    detail = outcome.error or "incomplete delivery"
                    print(
                        f"  Error sending '{job.get('title')}': {detail}",
                        file=sys.stderr,
                    )
            summary = _format_run_summary(
                stats,
                source_warning,
                cv_required=cv_required,
                telegram_markup=components.output_renderer.kind == "telegram",
            )
            try:
                notifier.send_message(summary)
            except Exception as exc:
                print("Output summary error: {}".format(exc), file=sys.stderr)

        print(llm.usage_summary(), flush=True)
        print("Done.", flush=True)
        return exit_code

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        try:
            notifier.send_error(exc)
        except Exception:
            pass
        raise
    finally:
        if cfg.state_sync and not test and state_mutation_allowed:
            # Push even on error: ordinary keys represent handled/silenced jobs,
            # while namespaced deferral markers only suppress duplicate notices.
            # Both are set-union-safe to share after a partial run.
            # Dev/test runs never push, mirroring run.sh's --list/--test rule.
            path = _seen_file(cfg)
            push_state() if path == SEEN_JOBS_FILE else push_state(seen_file=path)
