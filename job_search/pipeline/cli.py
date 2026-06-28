"""`python -m job_search.pipeline` entry point: argument parsing + dispatch."""
import argparse
import sys
import urllib.parse

from ..config import MIN_JOB_TEXT_LEN, PipelineConfig
from ..llm.clients import LLMClient
from ..notify.telegram import TelegramClient
from .run import run_daily, run_list, run_seed
from .stages import _send_error_notification, fetch_job_text_from_url, tailor_single_job


def run_tailor(args, cfg) -> None:
    """Entry point for `--tailor`: build one job dict, then tailor it."""
    if not all([cfg.gemini_api_key, cfg.telegram_bot_token, cfg.telegram_chat_id]):
        print("Error: GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        sys.exit(1)

    description = (args.job_text or "").strip()
    if not description and args.url:
        print(f"  Fetching job text from {args.url}", flush=True)
        description = fetch_job_text_from_url(args.url)

    if len(description) < MIN_JOB_TEXT_LEN:
        print(
            "Error: could not obtain enough job-description text "
            f"(got {len(description)} chars, need >= {MIN_JOB_TEXT_LEN}).\n"
            "The URL was likely blocked or requires JavaScript. Re-run and pass "
            "the description directly via --job-text (paste fallback).",
            file=sys.stderr,
        )
        sys.exit(1)

    company = (args.company or "").strip()
    if not company and args.url:
        host = urllib.parse.urlparse(args.url).netloc
        company = host.replace("www.", "").split(".")[0] if host else ""

    job = {
        "title": (args.title or "iOS Developer").strip(),
        "company": company or "the role",
        "location": (args.location or "").strip(),
        "url": (args.url or "").strip(),
        "description": description,
    }

    telegram = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    try:
        client = LLMClient(cfg.gemini_api_key, cfg.qwen_api_key)
        tailor_single_job(client, job, telegram)
        print("Done.", flush=True)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc, telegram)
        raise


def main():
    cfg = PipelineConfig.from_env()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test",
        action="store_true",
        help="Force-process one job through the full pipeline to verify everything works. Does not modify seen_jobs.json.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Fetch and print new jobs (not in seen_jobs.json) without AI evaluation or Telegram. Does not modify seen_jobs.json.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Mark all currently fetched jobs as seen without evaluating. Resets the baseline.",
    )
    parser.add_argument(
        "--tailor",
        action="store_true",
        help="Tailor a CV for ONE manually supplied job and send it to Telegram. "
             "Provide --url (auto-fetched) and/or --job-text (paste fallback). "
             "Does not touch seen_jobs.json.",
    )
    parser.add_argument("--url", default="", help="Job posting URL (used with --tailor).")
    parser.add_argument("--job-text", dest="job_text", default="",
                        help="Job description text, pasted directly (used with --tailor).")
    parser.add_argument("--title", default="", help="Job title (used with --tailor).")
    parser.add_argument("--company", default="", help="Company name (used with --tailor).")
    parser.add_argument("--location", default="", help="Job location (used with --tailor).")
    args = parser.parse_args()

    if args.tailor:
        run_tailor(args, cfg)
        return

    if args.seed:
        run_seed(cfg)
        return

    if args.list:
        run_list(cfg)
        return

    run_daily(cfg, test=args.test)


if __name__ == "__main__":
    main()
