"""Daily-run orchestration: seed / list / full pipeline.

Builds the LLM and Telegram clients once from the config and threads them into
the per-job stages. The seen-jobs state machine (first-run sentinel, staged
save points) is preserved exactly from the original pipeline.
"""
import concurrent.futures
import datetime
import sys

from ..config import load_base_tex, load_criteria, load_tailoring_instructions
from ..llm.clients import LLMClient
from ..llm.eval import evaluate_job
from ..models import job_to_dict
from ..notify.telegram import TelegramClient
from ..sources.fetch import fetch_jobs
from ..state.seen_jobs import (
    load_seen_jobs,
    normalize_url,
    save_seen_jobs,
    title_company_key,
)
from .stages import _send_error_notification, prepare_fit, process_job, send_fit


def run_seed(cfg) -> None:
    """Mark all currently fetched jobs as seen without evaluating."""
    raw_jobs = fetch_jobs(verbose=True)
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
    raw_jobs = fetch_jobs(verbose=True)
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
        raw_jobs = fetch_jobs(verbose=True)

        if test:
            if not raw_jobs:
                print("No jobs found — nothing to test.")
                return
            j = raw_jobs[0]
            d = job_to_dict(j)
            d["description"] = j.description
            print("Test mode: processing one job without touching seen_jobs.json.")
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

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # Gemini calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        fits = []  # list of (key, tc_key, job, evaluation) for jobs judged a fit
        if new_jobs:
            print(f"Evaluating {len(new_jobs)} job(s) with {cfg.eval_workers} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.eval_workers) as pool:
                future_to_job = {
                    pool.submit(evaluate_job, gemini, criteria, job): (key, tc_key, job)
                    for key, tc_key, job in new_jobs
                }
                for future in concurrent.futures.as_completed(future_to_job):
                    key, tc_key, job = future_to_job[future]
                    try:
                        evaluation = future.result()
                    except Exception as exc:
                        # Evaluation failed — leave unseen so it retries next run.
                        print(
                            f"  Error evaluating '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    if evaluation.get("fit"):
                        fits.append((key, tc_key, job, evaluation))
                    else:
                        # Not a fit: mark seen so it won't be reprocessed.
                        print(f"    Skip '{job.get('title')}' — {evaluation.get('reason', '')}")
                        seen.add(key)
                        seen.add(tc_key)
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
                        # prepare_fit swallows tailoring/compile errors internally,
                        # so this is unexpected — leave unseen to retry next run.
                        print(
                            f"  Error preparing '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    prepared.append((key, tc_key, payload))

        # ── Stage 4: Send to Telegram sequentially ───────────────────────────
        # Sequential to preserve message order and stay polite to the Telegram API.
        # A successful send marks the job seen; a failed send leaves it for retry.
        sent = 0
        for key, tc_key, payload in prepared:
            try:
                send_fit(payload, telegram)
                sent += 1
                seen.add(key)
                seen.add(tc_key)
                save_seen_jobs(seen)
            except Exception as exc:
                print(
                    f"  Error sending '{payload.get('title')}' — will retry next run: {exc}",
                    file=sys.stderr,
                )

        if sent == 0:
            noun = "new posting" if len(new_jobs) == 1 else "new postings"
            msg = (
                f"✅ Job search complete — {len(new_jobs)} {noun} checked, "
                f"none matched your criteria."
            )
            try:
                telegram.send_message(msg)
            except Exception as exc:
                print(f"Telegram notification error: {exc}", file=sys.stderr)

        print(gemini.usage_summary(), flush=True)
        print("Done.", flush=True)

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc, telegram)
        raise
