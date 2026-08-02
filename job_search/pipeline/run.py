"""Daily-run orchestration: seed / list / full pipeline.

Builds the LLM and Telegram clients once from the config and threads them into
the per-job stages. The seen-jobs state machine (first-run sentinel, staged
save points) is preserved exactly from the original pipeline.
"""
import concurrent.futures
import datetime
import html
import sys
from dataclasses import dataclass, field

from ..config import load_base_tex, load_criteria, load_tailoring_instructions
from ..digest import (
    DeferredEntry,
    DigestContext,
    FitEntry,
    ReviewEntry,
    build_digest_zip,
    cv_filename_for,
    digest_filename,
    load_sections,
)
from ..digest import publish_digest
from ..notify.telegraph import TelegraphClient
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
    criteria_version,
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
    _format_deferred_notification,
    _format_uncertain_notification,
    _send_error_notification,
    clean_job_description,
    ensure_job_description,
    prepare_fit,
    prepare_retry_fit,
    process_job,
    send_fit,
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


def _format_run_summary(stats: RunStats, source_warning="") -> str:
    lines = [
        "✅ <b>Job search complete</b>",
        f"New candidates: {stats.new_jobs}",
        f"Evaluated: {stats.evaluated} (non-fit: {stats.non_fit}, fit: {stats.fits})",
        f"Needs review (uncertain): {stats.uncertain}",
        f"Deferred: {stats.deferred}",
        f"Fit notifications sent: {stats.notification_sent}",
        f"Verified CVs delivered: {stats.cv_sent}",
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
    if stats.fits > stats.cv_sent:
        pending = stats.fits - stats.cv_sent
        noun = "fit" if pending == 1 else "fits"
        lines.extend(
            (
                "",
                f"⚠️ {pending} {noun} not fully delivered; they will retry according to scheduled backoff or remain blocked as shown below.",
            )
        )
    if stats.failure_details:
        lines.extend(("", "<b>Fit delivery failures</b>"))
        lines.extend(stats.failure_details[:10])
    if source_warning:
        lines.extend(("", "⚠️ <b>Source health</b>", html.escape(source_warning)))
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


def _drain_block_alerts(seen, telegram):
    for token, payload in pending_block_alerts(seen):
        try:
            telegram.send_message(_block_alert_message(payload))
        except Exception as exc:
            print(f"Telegram blocked-fit alert error: {exc}", file=sys.stderr)
            continue
        acknowledge_block_alert(seen, token)
        save_seen_jobs(seen)


def _record_fit_failure(seen, stats, job, stage, today, telegram):
    previous = delivery_retry_state(seen, **job)
    state = record_delivery_failure(seen, job, today, stage)
    save_seen_jobs(seen)
    if len(stats.failure_details) < 10:
        stats.failure_details.append(_failure_detail(job, stage, state))
    if state.blocked and not previous.blocked:
        stats.newly_blocked += 1
        _drain_block_alerts(seen, telegram)
    return state


def _evaluate_candidate(llm, criteria, job):
    """Evaluate a candidate only when its cleaned description is sufficient."""
    if not ensure_job_description(job):
        return None
    return evaluate_job(llm, criteria, job)


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
    report = fetch_jobs_with_health(
        source_names=select_sources(cfg.sources_enable, cfg.sources_disable), verbose=True,
    )
    summary = format_source_health(report)
    if summary:
        print(summary, file=sys.stderr)
    return report


def run_seed(cfg) -> int:
    """Mark all currently fetched jobs as seen without evaluating."""
    report = _fetch_for_pipeline(cfg)
    if not report.has_usable_source:
        print("No usable job source completed; seed aborted.", file=sys.stderr)
        return 1
    raw_jobs = report.jobs
    seen_raw = load_seen_jobs()
    seen = seen_raw if seen_raw is not None else SeenSet()
    added = 0
    for j in raw_jobs:
        keys = job_identity_keys(j)
        url_key = normalize_url(j.url)
        if url_key and url_key not in seen:
            added += 1
        seen.update(keys)
    save_seen_jobs(seen)
    print(f"Seed complete — {len(raw_jobs)} job(s) marked seen ({added} new URL entries added).")
    return 0


def run_list(cfg) -> int:
    """Fetch and print new jobs (not in seen_jobs.json) without AI/Telegram."""
    report = _fetch_for_pipeline(cfg)
    if not report.has_usable_source:
        print("No usable job source completed; list aborted.", file=sys.stderr)
        return 1
    raw_jobs = report.jobs
    seen_raw = load_seen_jobs()
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


def _summaries(llm, jobs, workers):
    """One short LLM summary per job, computed concurrently (aligned with jobs)."""
    if not jobs:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda job: summarize_job(llm, job), jobs))


def _digest_caption(n_fits, n_review, n_deferred, date, page_url="") -> str:
    # Counts come from what actually went into the digest, not the run-level
    # stats (a fit whose CV failed to compile is not included).
    bits = ["{} fit{}".format(n_fits, "" if n_fits == 1 else "s")]
    if n_review:
        bits.append(f"{n_review} to review")
    if n_deferred:
        bits.append(f"{n_deferred} deferred")
    tail = page_url or "Open index.html in the archive."
    return "✅ Job Search Digest — {}\n{}\n{}".format(
        date.isoformat(), " · ".join(bits), tail
    )


# Telegram's bot sendDocument ceiling. A real run bundles a handful of ~40 KB
# CVs (well under 1 MB), so this only guards a pathological batch; we log rather
# than split, since exceeding it here would mean something is badly wrong.
_TELEGRAM_DOC_LIMIT = 50 * 1024 * 1024


def _commit_deferrals(seen, deferrals, today) -> None:
    """Record queued deferrals: markers to suppress repeat notices, plus the
    signature that stops a reopened job from re-deferring every run."""
    for markers, job, signature in deferrals:
        seen.update(markers)
        if signature is not None:
            record_evaluation(seen, job, signature, "deferred", today)
    if deferrals:
        save_seen_jobs(seen)


def _deliver_digest(
    llm, cfg, seen, stats, today, prepared, uncertain, newly_deferred,
    source_warning, telegram, signature_for, deferrals=(),
):
    """Bundle a run into ONE ZIP (HTML dashboard + tailored CVs) and send it once.

    On a successful send every included fit is marked delivered/seen exactly as
    the per-job path does; on a failed send each fit records a delivery failure
    so it retries next run (the day+1/day+2/block-after-3 machinery is reused).

    ``deferrals`` is the list of (markers, job, signature) tuples for jobs whose
    deferral is *announced by this ZIP*. Like the review section, they are only
    committed to ``seen`` once the send succeeds — recording them earlier marks
    the jobs "already announced" while the notice is still undelivered
    (finding N9).
    """
    fit_jobs = [job for job, _payload, _rs, _ev in prepared]
    review_jobs = [job for job, _ev in uncertain]
    summaries = _summaries(llm, fit_jobs + review_jobs, cfg.eval_workers)
    fit_summaries = summaries[: len(fit_jobs)]
    review_summaries = summaries[len(fit_jobs):]

    taken = set()
    fit_entries = []
    for (job, payload, _rs, evaluation), summary in zip(prepared, fit_summaries):
        name = cv_filename_for(job, taken)
        taken.add(name)
        fit_entries.append(
            FitEntry(
                job=job,
                evaluation=evaluation,
                summary=summary,
                pdf_bytes=payload.get("pdf_bytes"),
                cv_filename=name,
            )
        )
    review_entries = [
        ReviewEntry(job=job, evaluation=evaluation, summary=summary)
        for (job, evaluation), summary in zip(uncertain, review_summaries)
    ]
    deferred_entries = [DeferredEntry(job=job) for job in newly_deferred]

    if not (fit_entries or review_entries or deferred_entries):
        # Nothing to bundle. Any pending deferral still has to be committed:
        # reaching here with a non-empty `deferrals` means every one of them had
        # markers already in `seen` (that is exactly why it isn't in
        # newly_deferred / deferred_entries), so its notice went out in an
        # earlier run and there is nothing left to wait for. Skipping them —
        # which is what happened before this early return committed them — loses
        # the "deferred" signature that stops the reopen→defer cycle, and the job
        # re-pays the description fetch every run forever.
        _commit_deferrals(seen, deferrals, today)
        # Keep the lightweight text completion notice.
        try:
            telegram.send_message(_format_run_summary(stats, source_warning))
        except Exception as exc:
            print(f"Telegram notification error: {exc}", file=sys.stderr)
        return

    # Loaded here rather than in run_daily so the legacy per-job delivery path
    # never pays for it, and so a config problem is announced only on a run that
    # actually had something to group.
    sections, sections_error = load_sections(cfg.sections_file)
    if sections_error:
        print("  Digest sections: {}".format(sections_error), file=sys.stderr)
        try:
            telegram.send_message(
                "⚠️ Digest sections: {}".format(html.escape(sections_error))
            )
        except Exception as exc:
            print(f"Telegram sections alert error: {exc}", file=sys.stderr)

    ctx = DigestContext(
        date=today,
        stats=stats,
        source_warning=source_warning,
        # The state-size line rides along here so the growth trend is visible in
        # the archive itself, not only in a CI log nobody reads.
        usage_summary=" ".join((llm.usage_summary(), state_size_summary(seen))),
        fits=fit_entries,
        review=review_entries,
        deferred=deferred_entries,
        sections=sections,
        sections_error=sections_error,
    )
    # Telegraph first: a page plus a link message is the whole digest. Any
    # failure here (no token, API down, content rejected) falls through to the
    # ZIP, which is the same delivery this pipeline has always done.
    page_url = ""
    if cfg.telegraph_access_token:
        try:
            page_url = publish_digest(
                TelegraphClient(), cfg.telegraph_access_token, ctx, today
            )
        except Exception as exc:
            print(
                f"  Telegraph publish failed — falling back to the ZIP: {exc}",
                file=sys.stderr,
            )

    caption = _digest_caption(
        len(fit_entries), len(review_entries), len(deferred_entries), today, page_url
    )
    if not page_url:
        zip_bytes = build_digest_zip(ctx)
        if len(zip_bytes) > _TELEGRAM_DOC_LIMIT:
            # Diagnosable rather than silent: the send below will fail and the
            # fits will retry, but at least the log says why.
            print(
                f"  Digest is {len(zip_bytes) // (1024 * 1024)} MB, over Telegram's "
                f"{_TELEGRAM_DOC_LIMIT // (1024 * 1024)} MB limit — send will likely fail.",
                file=sys.stderr,
            )
    try:
        if page_url:
            telegram.send_message(caption)
        else:
            telegram.send_document(digest_filename(today), zip_bytes, caption)
    except Exception as exc:
        # Whole-batch delivery failed: every fit stays unseen and retries.
        print(f"  Digest delivery failed — fits will retry next run: {exc}", file=sys.stderr)
        for job, _payload, _rs, _ev in prepared:
            stats.delivery_failed += 1
            _record_fit_failure(seen, stats, job, "delivery", today, telegram)
            # Mark notified so the retry takes the known-fit route: the fallback
            # run summary below names each pending fit, and without this the next
            # run re-pays fact extraction, bullet selection AND pdflatex from
            # scratch for a job it already knows is a fit (finding N9).
            mark_delivery_notified(seen, **job)
        if prepared:
            save_seen_jobs(seen)
        # Never leave the user in silence — the digest was the only delivery, so
        # fall back to the text run summary (which reports the delivery failures
        # and the pending retries).
        try:
            telegram.send_message(_format_run_summary(stats, source_warning))
        except Exception as summary_exc:
            print(f"Telegram fallback summary error: {summary_exc}", file=sys.stderr)
        return

    for entry, (job, _payload, retry_state, _ev) in zip(fit_entries, prepared):
        if page_url:
            # The page has no file hosting, so each CV rides its own document.
            # A failure here is per-fit: the digest itself is already delivered,
            # so only this job goes back on the retry ladder rather than the run.
            try:
                telegram.send_document(
                    entry.cv_filename,
                    entry.pdf_bytes,
                    "Tailored CV — {} at {}".format(
                        coerce_job(job).title, coerce_job(job).company
                    ),
                )
            except Exception as exc:
                print(
                    f"  CV delivery failed for '{coerce_job(job).title}' — "
                    f"retrying next run: {exc}",
                    file=sys.stderr,
                )
                stats.delivery_failed += 1
                _record_fit_failure(seen, stats, job, "document", today, telegram)
                mark_delivery_notified(seen, **job)
                continue
        # Only count a notification the user is actually seeing for the first
        # time; a retried fit was announced in an earlier run, and the legacy
        # path avoids double-counting the same way (finding N9).
        stats.notification_sent += int(not (retry_state and retry_state.notified))
        stats.cv_sent += 1
        mark_delivery_notified(seen, **job)
        seen.update(job_identity_keys(job))
        sig = signature_for(job)
        if sig is not None:
            record_evaluation(seen, job, sig, "fit", today)
    # One write for the whole batch rather than one per fit: save_seen_jobs
    # re-sorts and rewrites the entire file, and every key here is union-safe, so
    # a crash mid-loop is already covered by the retry ladder.
    if prepared:
        save_seen_jobs(seen)

    # The review and deferral sections rode on this same (now-delivered) ZIP, so
    # it is finally safe to record them — a failed send above returns early and
    # leaves them unrecorded to re-surface next run.
    for job, evaluation in uncertain:
        seen.update(job_identity_keys(job))
        sig = signature_for(job)
        if sig is not None:
            record_evaluation(seen, job, sig, "uncertain", today)
    if uncertain:
        save_seen_jobs(seen)
    _commit_deferrals(seen, deferrals, today)


def run_daily(cfg, test: bool = False) -> int:
    """The full scheduled pipeline: fetch → evaluate → tailor → deliver."""
    if cfg.state_sync:
        # Pull the shared dedup baseline FIRST: linkedin-guest peeks at
        # seen_jobs.json during fetch to skip description requests for jobs the
        # other runner already delivered. Best-effort; never raises.
        pull_state()
    if not all([cfg.llm_primary_api_key, cfg.telegram_bot_token, cfg.telegram_chat_id]):
        print(
            "Error: the primary LLM API key (LLM_PRIMARY_API_KEY / GEMINI_API_KEY), "
            "TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    telegram = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    state_mutation_allowed = False
    try:
        llm = LLMClient.from_config(cfg)
        shutdown_note = model_shutdown_warning(cfg.llm_primary_model)
        if shutdown_note:
            print(shutdown_note, flush=True)
        if not cfg.llm_fallback_api_key:
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
        criteria = load_criteria()
        crit_ver = criteria_version(criteria)
        tailoring_instructions = load_tailoring_instructions()
        base_tex = load_base_tex()

        print("Fetching jobs...", flush=True)
        report = _fetch_for_pipeline(cfg)
        if not report.has_usable_source:
            message = "🚨 <b>Job source outage</b>\nNo selected source completed successfully; evaluation and state updates were aborted."
            print("No usable job source completed; daily run aborted.", file=sys.stderr)
            try:
                telegram.send_message(message)
            except Exception as exc:
                print(f"Telegram source-outage notification error: {exc}", file=sys.stderr)
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
                    telegram.send_message(_format_deferred_notification([d]))
                except Exception as exc:
                    print(
                        f"Telegram deferred-job notification error: {exc}",
                        file=sys.stderr,
                    )
                print("Done.", flush=True)
                return 0
            process_job(llm, criteria, tailoring_instructions, base_tex, d, telegram)
            print("Done.", flush=True)
            return 0

        seen_raw = load_seen_jobs()
        first_run = seen_raw is None
        seen = seen_raw if seen_raw is not None else SeenSet()
        # Reported, never pruned: the eval:* markers are order 4d's reopen
        # history. Logging the growth every run means a future pruning decision
        # rests on real numbers.
        print(state_size_summary(seen), flush=True)
        _drain_block_alerts(seen, telegram)

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
        save_seen_jobs(seen)
        print(f"Found {candidate_count} new or retryable job(s).", flush=True)

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # LLM calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        if evaluation_jobs:
            print(f"Evaluating {len(evaluation_jobs)} job(s) with {cfg.eval_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.eval_workers) as pool:
                future_to_job = {
                    pool.submit(_evaluate_candidate, llm, criteria, job): (job, retry_state)
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
                    telegram.send_message(_format_deferred_notification(newly_deferred))
                except Exception as exc:
                    print(
                        f"Telegram deferred-job notification error: {exc}",
                        file=sys.stderr,
                    )
            if uncertain:
                try:
                    telegram.send_message(_format_uncertain_notification(uncertain))
                except Exception as exc:
                    print(
                        f"Telegram uncertain-job notification error: {exc}",
                        file=sys.stderr,
                    )
        # Persist the non-fits and uncertain jobs captured above in one write.
        save_seen_jobs(seen)
        print(f"{len(fits)} fit(s) to tailor.", flush=True)

        # ── Stage 3: Tailor + compile the fits concurrently ──────────────────
        # Tailoring is LLM-bound and compilation is CPU-bound; a smaller pool
        # keeps parallel pdflatex runs from starving the runner. No Telegram I/O
        # happens here, so order doesn't matter and failures stay soft.
        prepared = []  # list of (job, payload, retry_state, evaluation) ready to send
        if fits:
            print(f"Tailoring {len(fits)} CV(s) with {cfg.tailor_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.tailor_workers) as pool:
                future_to_fit = {}
                for job, evaluation, retry_state in fits:
                    if retry_state.notified:
                        future = pool.submit(
                            prepare_retry_fit,
                            llm,
                            tailoring_instructions,
                            base_tex,
                            job,
                        )
                    else:
                        future = pool.submit(
                            prepare_fit,
                            llm,
                            tailoring_instructions,
                            base_tex,
                            job,
                            evaluation,
                        )
                    future_to_fit[future] = (job, evaluation, retry_state)
                for future in concurrent.futures.as_completed(future_to_fit):
                    job, evaluation, retry_state = future_to_fit[future]
                    try:
                        payload = future.result()
                    except Exception as exc:
                        stats.preparation_failed += 1
                        failure_state = _record_fit_failure(
                            seen,
                            stats,
                            job,
                            "preparation",
                            today,
                            telegram,
                        )
                        print(
                            f"  Error preparing '{job.get('title')}' — {failure_state}: {exc}",
                            file=sys.stderr,
                        )
                        if failure_state.attempt == 1 and not retry_state.notified:
                            try:
                                telegram.send_message(
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
                                save_seen_jobs(seen)
                        continue
                    prepared.append((job, payload, retry_state, evaluation))

        # ── Stage 4: Deliver ─────────────────────────────────────────────────
        if cfg.digest_delivery:
            # One ZIP per run: HTML dashboard + every tailored CV, folding in the
            # uncertain and deferred jobs too. All state reconciliation (mark
            # seen on success, record delivery failure on send error) happens
            # inside _deliver_digest.
            _deliver_digest(
                llm, cfg, seen, stats, today, prepared, uncertain, newly_deferred,
                source_warning, telegram, _signature_for, pending_deferrals,
            )
        else:
            # Legacy per-job path (DIGEST_DELIVERY=0): a notification + PDF per
            # fit, sequentially, then a text run summary.
            for job, payload, retry_state, _evaluation in prepared:
                try:
                    outcome = send_fit(
                        payload,
                        telegram,
                        notification_already_sent=retry_state.notified,
                    )
                except Exception as exc:
                    stats.delivery_failed += 1
                    _record_fit_failure(seen, stats, job, "notification", today, telegram)
                    print(
                        f"  Error sending '{payload.get('title')}': {exc}",
                        file=sys.stderr,
                    )
                    continue

                stats.notification_sent += int(outcome.notification_sent)
                stats.cv_sent += int(outcome.cv_sent)
                if outcome.notification_sent:
                    mark_delivery_notified(seen, **job)
                    save_seen_jobs(seen)
                if outcome.complete:
                    seen.update(job_identity_keys(job))
                    signature = _signature_for(job)
                    if signature is not None:
                        record_evaluation(seen, job, signature, "fit", today)
                    save_seen_jobs(seen)
                else:
                    stats.delivery_failed += 1
                    stage = "document" if outcome.notification_satisfied else "notification"
                    _record_fit_failure(seen, stats, job, stage, today, telegram)
                    detail = outcome.error or "incomplete delivery"
                    print(
                        f"  Error sending '{payload.get('title')}': {detail}",
                        file=sys.stderr,
                    )

            try:
                telegram.send_message(_format_run_summary(stats, source_warning))
            except Exception as exc:
                print(f"Telegram notification error: {exc}", file=sys.stderr)

        print(llm.usage_summary(), flush=True)
        print("Done.", flush=True)
        return 0

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc, telegram)
        raise
    finally:
        if cfg.state_sync and not test and state_mutation_allowed:
            # Push even on error: ordinary keys represent handled/silenced jobs,
            # while namespaced deferral markers only suppress duplicate notices.
            # Both are set-union-safe to share after a partial run.
            # Dev/test runs never push, mirroring run.sh's --list/--test rule.
            push_state()
