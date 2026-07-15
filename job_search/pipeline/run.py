"""Daily-run orchestration: seed / list / full pipeline.

Builds the LLM and Telegram clients once from the config and threads them into
the per-job stages. The seen-jobs state machine (first-run sentinel, staged
save points) is preserved exactly from the original pipeline.
"""
import concurrent.futures
import datetime
import sys
from dataclasses import dataclass

from ..config import load_base_tex, load_criteria, load_tailoring_instructions
from ..llm.clients import LLMClient
from ..llm.eval import evaluate_job
from ..models import job_to_dict
from ..notify.telegram import TelegramClient
from ..sources.fetch import fetch_jobs, select_sources
from ..state.git_sync import pull_state, push_state
from ..state.seen_jobs import (
    load_seen_jobs,
    normalize_url,
    save_seen_jobs,
    title_company_key,
)
from .stages import (
    _format_deferred_notification,
    _send_error_notification,
    ensure_job_description,
    prepare_fit,
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
    ]
    if stats.fits == 0:
        lines.extend(("", "No evaluated jobs matched your criteria."))
    if stats.fits > stats.cv_sent:
        pending = stats.fits - stats.cv_sent
        noun = "fit" if pending == 1 else "fits"
        lines.extend(("", f"⚠️ {pending} {noun} not fully delivered; they will retry next run."))
    return "\n".join(lines)


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
        gemini = LLMClient(cfg.gemini_api_key, cfg.qwen_api_key)
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
            d = job_to_dict(j)
            d["description"] = j.description
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

        cutoff = datetime.date.today() - datetime.timedelta(days=7)

        # new_jobs: list of (url_key, tc_key, job_dict)
        # Keys are NOT added to seen yet — added only after successful processing.
        new_jobs = []
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
            d = job_to_dict(j)
            d["description"] = j.description
            new_jobs.append((key, tc_key, d))

        # Persist seen set now — captures first-run silenced jobs; new jobs are NOT yet included.
        save_seen_jobs(seen)
        print(f"Found {len(new_jobs)} new job(s).", flush=True)
        stats = RunStats(new_jobs=len(new_jobs))

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # Gemini calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        fits = []  # list of (key, tc_key, job, evaluation) for jobs judged a fit
        newly_deferred = []
        if new_jobs:
            print(f"Evaluating {len(new_jobs)} job(s) with {cfg.eval_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.eval_workers) as pool:
                future_to_job = {
                    pool.submit(_evaluate_candidate, gemini, criteria, job): (
                        key,
                        tc_key,
                        job,
                    )
                    for key, tc_key, job in new_jobs
                }
                for future in concurrent.futures.as_completed(future_to_job):
                    key, tc_key, job = future_to_job[future]
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
                    stats.evaluated += 1
                    if evaluation.get("fit"):
                        stats.fits += 1
                        fits.append((key, tc_key, job, evaluation))
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
        prepared = []  # list of (key, tc_key, payload) ready to send
        if fits:
            print(f"Tailoring {len(fits)} CV(s) with {cfg.tailor_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.tailor_workers) as pool:
                future_to_fit = {
                    pool.submit(prepare_fit, gemini, tailoring_instructions, base_tex, job, evaluation): (key, tc_key, job)
                    for key, tc_key, job, evaluation in fits
                }
                for future in concurrent.futures.as_completed(future_to_fit):
                    key, tc_key, job = future_to_fit[future]
                    try:
                        payload = future.result()
                    except Exception as exc:
                        stats.preparation_failed += 1
                        print(
                            f"  Error preparing '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    prepared.append((key, tc_key, payload))

        # ── Stage 4: Send to Telegram sequentially ───────────────────────────
        # Sequential to preserve message order and stay polite to the Telegram API.
        # A successful send marks the job seen; a failed send leaves it for retry.
        for key, tc_key, payload in prepared:
            try:
                outcome = send_fit(payload, telegram)
            except Exception as exc:
                stats.delivery_failed += 1
                print(
                    f"  Error sending '{payload.get('title')}' — will retry next run: {exc}",
                    file=sys.stderr,
                )
                continue

            stats.notification_sent += int(outcome.notification_sent)
            stats.cv_sent += int(outcome.cv_sent)
            if outcome.complete:
                seen.add(key)
                seen.add(tc_key)
                save_seen_jobs(seen)
            else:
                stats.delivery_failed += 1
                detail = outcome.error or "incomplete delivery"
                print(
                    f"  Error sending '{payload.get('title')}' — will retry next run: {detail}",
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
