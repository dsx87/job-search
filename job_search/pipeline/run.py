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
from ..llm.clients import LLMClient
from ..llm.eval import evaluate_job
from ..models import coerce_job
from ..notify.telegram import TelegramClient
from ..sources.fetch import fetch_jobs, select_sources
from ..state.git_sync import pull_state, push_state
from ..state.seen_jobs import (
    acknowledge_block_alert,
    delivery_retry_state,
    load_seen_jobs,
    mark_delivery_notified,
    normalize_url,
    pending_block_alerts,
    record_delivery_failure,
    save_seen_jobs,
    title_company_key,
)
from .stages import (
    _format_deferred_notification,
    _send_error_notification,
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


def _format_run_summary(stats: RunStats) -> str:
    lines = [
        "✅ <b>Job search complete</b>",
        f"New candidates: {stats.new_jobs}",
        f"Evaluated: {stats.evaluated} (non-fit: {stats.non_fit}, fit: {stats.fits})",
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


def _evaluate_candidate(gemini, criteria, job):
    """Evaluate a candidate only when its cleaned description is sufficient."""
    if not ensure_job_description(job):
        return None
    return evaluate_job(gemini, criteria, job)


# Reserved for pipeline metadata; these entries must never act as seen identities.
_DEFERRED_MARKER_PREFIX = "deferred:"


def _deferred_markers(key: str, tc_key: str) -> set[str]:
    markers = set()
    if key:
        markers.add(f"{_DEFERRED_MARKER_PREFIX}url:{key}")
    if tc_key and tc_key != "|":
        markers.add(f"{_DEFERRED_MARKER_PREFIX}job:{tc_key}")
    return markers


def run_seed(cfg) -> None:
    """Mark all currently fetched jobs as seen without evaluating."""
    raw_jobs = fetch_jobs(source_names=select_sources(cfg.sources_enable, cfg.sources_disable), verbose=True)
    seen_raw = load_seen_jobs()
    seen = seen_raw if seen_raw is not None else set()
    added = 0
    for j in raw_jobs:
        key = normalize_url(j.url)
        tc_key = title_company_key(j.title, j.company, j.location)
        if key not in seen:
            seen.add(key)
            added += 1
        if tc_key not in seen:
            seen.add(tc_key)
    save_seen_jobs(seen)
    print(f"Seed complete — {len(raw_jobs)} job(s) marked seen ({added} new URL entries added).")


def run_list(cfg) -> None:
    """Fetch and print new jobs (not in seen_jobs.json) without AI/Telegram."""
    raw_jobs = fetch_jobs(source_names=select_sources(cfg.sources_enable, cfg.sources_disable), verbose=True)
    seen_raw = load_seen_jobs()
    seen = seen_raw if seen_raw is not None else set()
    new_jobs = [j for j in raw_jobs if normalize_url(j.url) not in seen and title_company_key(j.title, j.company, j.location) not in seen]
    print(f"{len(new_jobs)} new job(s):\n")
    for j in new_jobs:
        date_str = j.date_posted.strftime("%Y-%m-%d") if j.date_posted else "n/a"
        print(f"  {j.title}")
        print(f"  {j.company} | {j.location or 'n/a'} | {j.source} | {date_str}")
        print(f"  {j.url}")
        print()


def run_daily(cfg, test: bool = False) -> None:
    """The full scheduled pipeline: fetch → evaluate → tailor → deliver."""
    if cfg.state_sync:
        # Pull the shared dedup baseline FIRST: linkedin-guest peeks at
        # seen_jobs.json during fetch to skip description requests for jobs the
        # other runner already delivered. Best-effort; never raises.
        pull_state()
    if not all([cfg.gemini_api_key, cfg.telegram_bot_token, cfg.telegram_chat_id]):
        print("Error: GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        sys.exit(1)

    telegram = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    try:
        gemini = LLMClient(
            cfg.gemini_api_key,
            cfg.qwen_api_key,
            gemini_model=cfg.gemini_model,
            gemini_api_base=cfg.gemini_api_base,
            qwen_model=cfg.qwen_model,
            qwen_api_base=cfg.qwen_api_base,
        )
        if not cfg.qwen_api_key:
            print("Note: QWEN_API_KEY not set — no fallback model available.", flush=True)
        criteria = load_criteria()
        tailoring_instructions = load_tailoring_instructions()
        base_tex = load_base_tex()

        print("Fetching jobs...", flush=True)
        raw_jobs = fetch_jobs(source_names=select_sources(cfg.sources_enable, cfg.sources_disable), verbose=True)

        if test:
            if not raw_jobs:
                print("No jobs found — nothing to test.")
                return
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
                return
            process_job(gemini, criteria, tailoring_instructions, base_tex, d, telegram)
            print("Done.", flush=True)
            return

        seen_raw = load_seen_jobs()
        first_run = seen_raw is None
        seen = seen_raw if seen_raw is not None else set()
        _drain_block_alerts(seen, telegram)

        today = _today()
        cutoff = today - datetime.timedelta(days=7)

        stats = RunStats()
        # evaluation_jobs: list of (url_key, tc_key, Job, retry_state)
        # Keys are NOT added to seen yet — added only after successful processing.
        evaluation_jobs = []
        fits = []
        newly_deferred = []
        candidate_count = 0
        for j in raw_jobs:
            key = normalize_url(j.url)
            tc_key = title_company_key(j.title, j.company, j.location)
            if key in seen or tc_key in seen:
                continue
            if first_run:
                # On first run, silently mark jobs older than 7 days as seen without evaluating.
                dp = j.date_posted
                posted = dp.date() if isinstance(dp, datetime.datetime) else dp  # may be date or None
                if posted is not None and posted < cutoff:
                    seen.add(key)
                    seen.add(tc_key)
                    continue
            d = coerce_job(j)
            retry_state = delivery_retry_state(seen, **d)
            if retry_state.blocked:
                continue
            candidate_count += 1
            if retry_state.attempt == 0 and not retry_state.notified:
                stats.new_jobs += 1
            if retry_state.retry_on and retry_state.retry_on > today:
                stats.retries_waiting += 1
                continue
            if retry_state.notified:
                if not ensure_job_description(d):
                    stats.deferred += 1
                    markers = _deferred_markers(key, tc_key)
                    if not markers or markers.isdisjoint(seen):
                        newly_deferred.append(d)
                    seen.update(markers)
                    continue
                stats.retry_attempts += int(retry_state.attempt > 0)
                stats.known_fit_retries += 1
                stats.fits += 1
                fits.append((key, tc_key, d, None, retry_state))
                continue
            evaluation_jobs.append((key, tc_key, d, retry_state))

        # Persist seen set now — captures first-run silenced jobs; new jobs are NOT yet included.
        save_seen_jobs(seen)
        print(f"Found {candidate_count} new or retryable job(s).", flush=True)

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # Gemini calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        if evaluation_jobs:
            print(f"Evaluating {len(evaluation_jobs)} job(s) with {cfg.eval_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.eval_workers) as pool:
                future_to_job = {
                    pool.submit(_evaluate_candidate, gemini, criteria, job): (
                        key,
                        tc_key,
                        job,
                        retry_state,
                    )
                    for key, tc_key, job, retry_state in evaluation_jobs
                }
                for future in concurrent.futures.as_completed(future_to_job):
                    key, tc_key, job, retry_state = future_to_job[future]
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
                        stats.deferred += 1
                        markers = _deferred_markers(key, tc_key)
                        if not markers or markers.isdisjoint(seen):
                            newly_deferred.append(job)
                        seen.update(markers)
                        continue
                    stats.retry_attempts += int(retry_state.attempt > 0)
                    stats.evaluated += 1
                    if evaluation.get("fit"):
                        stats.fits += 1
                        fits.append((key, tc_key, job, evaluation, retry_state))
                    else:
                        # Not a fit: mark seen so it won't be reprocessed.
                        stats.non_fit += 1
                        print(f"    Skip '{job.get('title')}' — {evaluation.get('reason', '')}")
                        seen.add(key)
                        seen.add(tc_key)
        if stats.deferred:
            print(
                f"Deferred {stats.deferred} job(s) with insufficient description text.",
                flush=True,
            )
        if newly_deferred:
            try:
                telegram.send_message(_format_deferred_notification(newly_deferred))
            except Exception as exc:
                print(
                    f"Telegram deferred-job notification error: {exc}",
                    file=sys.stderr,
                )
        # Persist the non-fits captured above in one write.
        save_seen_jobs(seen)
        print(f"{len(fits)} fit(s) to tailor.", flush=True)

        # ── Stage 3: Tailor + compile the fits concurrently ──────────────────
        # Tailoring is Gemini-bound and compilation is CPU-bound; a smaller pool
        # keeps parallel xelatex runs from starving the runner. No Telegram I/O
        # happens here, so order doesn't matter and failures stay soft.
        prepared = []  # list of (key, tc_key, job, payload, retry_state) ready to send
        if fits:
            print(f"Tailoring {len(fits)} CV(s) with {cfg.tailor_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.tailor_workers) as pool:
                future_to_fit = {}
                for key, tc_key, job, evaluation, retry_state in fits:
                    if retry_state.notified:
                        future = pool.submit(
                            prepare_retry_fit,
                            gemini,
                            tailoring_instructions,
                            base_tex,
                            job,
                        )
                    else:
                        future = pool.submit(
                            prepare_fit,
                            gemini,
                            tailoring_instructions,
                            base_tex,
                            job,
                            evaluation,
                        )
                    future_to_fit[future] = (key, tc_key, job, evaluation, retry_state)
                for future in concurrent.futures.as_completed(future_to_fit):
                    key, tc_key, job, evaluation, retry_state = future_to_fit[future]
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
                    prepared.append((key, tc_key, job, payload, retry_state))

        # ── Stage 4: Send to Telegram sequentially ───────────────────────────
        # Sequential to preserve message order and stay polite to the Telegram API.
        # A successful send marks the job seen; a failed send leaves it for retry.
        for key, tc_key, job, payload, retry_state in prepared:
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
                seen.add(key)
                seen.add(tc_key)
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
            telegram.send_message(_format_run_summary(stats))
        except Exception as exc:
            print(f"Telegram notification error: {exc}", file=sys.stderr)

        print(gemini.usage_summary(), flush=True)
        print("Done.", flush=True)

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc, telegram)
        raise
    finally:
        if cfg.state_sync and not test:
            # Push even on error: ordinary keys represent handled/silenced jobs,
            # while namespaced deferral markers only suppress duplicate notices.
            # Both are set-union-safe to share after a partial run.
            # Dev/test runs never push, mirroring run.sh's --list/--test rule.
            push_state()
