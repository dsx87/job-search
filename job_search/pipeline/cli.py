"""`python -m job_search.pipeline` entry point: argument parsing + dispatch."""
import argparse
import sys
import urllib.parse

from ..config import (
    BASE_TEX_FILE,
    CV_TAILORING_PROMPT_FILE,
    MIN_JOB_TEXT_LEN,
    PipelineConfig,
)
from ..composition import ConfigurationError, load_components, redacted_configuration
from ..components import DefaultOutputBackend, DefaultOutputRenderer
from ..llm.clients import LLMClient
from ..models import Job
from ..notify.telegram import TelegramClient
from .run import run_daily, run_list, run_seed
from .stages import (
    CVDeliveryError,
    _send_error_notification,
    ensure_job_description,
    tailor_single_job,
)


def _notify_composed_error(components, exc):
    try:
        notice = "{}: {}".format(type(exc).__name__, exc)
        components.output_backend.deliver_notice(
            components.output_renderer.render_notice(
                notice,
                level="error",
                title="Pipeline error",
                icon="⚠️",
                code=True,
            )
        )
    except Exception:
        pass


def run_tailor(args, cfg) -> None:
    """Entry point for `--tailor`: build one Job, then tailor it."""
    components = load_components(cfg, command="tailor")

    company = (args.company or "").strip()
    if not company and args.url:
        host = urllib.parse.urlparse(args.url).netloc
        company = host.replace("www.", "").split(".")[0] if host else ""

    job = Job(
        title=args.title or "iOS Developer",
        company=company or "the role",
        location=args.location,
        url=args.url,
        description=args.job_text,
    )

    if not ensure_job_description(job):
        print(
            "Error: could not obtain enough job-description text "
            f"(got {len(job.description)} chars, need >= {MIN_JOB_TEXT_LEN}).\n"
            "The URL was likely blocked or requires JavaScript. Re-run and pass "
            "the description directly via --job-text (paste fallback).",
            file=sys.stderr,
        )
        sys.exit(1)

    custom_output = (
        getattr(components, "_customized", False)
        and type(components.output_backend) is not DefaultOutputBackend
    )
    use_configured_tailoring = (
        getattr(components, "_customized", False)
        or getattr(cfg, "base_tex_file", BASE_TEX_FILE) != BASE_TEX_FILE
        or getattr(
            cfg, "cv_tailoring_prompt_file", CV_TAILORING_PROMPT_FILE
        ) != CV_TAILORING_PROMPT_FILE
    )
    if custom_output:
        client = components.llm
        try:
            artifact = components.cv_renderer.render_tailored(client, job)
            rendered = components.output_renderer.render_fit(
                job, {"reason": "Manual tailoring"}
            )
            outcome = components.output_backend.deliver_fit(rendered, artifact)
            if not outcome.complete:
                raise CVDeliveryError(outcome)
            print("Done.", flush=True)
            return
        except Exception as exc:
            print(f"Fatal error: {exc}", file=sys.stderr)
            _notify_composed_error(components, exc)
            raise
    if use_configured_tailoring:
        client = components.llm
        telegram = components.output_backend.telegram
    else:
        # Compatibility seam for callers/tests that patch the historical
        # factories directly; custom composition uses the configured objects.
        client = LLMClient.from_config(cfg)
        telegram = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    try:
        if use_configured_tailoring:
            tailor_single_job(
                client,
                job,
                telegram,
                renderer=components.cv_renderer,
                fit_renderer=(
                    components.output_renderer
                    if (
                        getattr(components, "_customized", False)
                        and type(components.output_renderer)
                        is not DefaultOutputRenderer
                    )
                    else None
                ),
            )
        else:
            tailor_single_job(client, job, telegram)
        print("Done.", flush=True)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        if use_configured_tailoring:
            _notify_composed_error(components, exc)
        else:
            _send_error_notification(exc, telegram)
        raise


def main():
    cfg = PipelineConfig.from_env()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Load and validate configuration, print redacted effective settings, and exit.",
    )
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

    if args.check_config:
        try:
            components = load_components(cfg, command="check")
        except ConfigurationError as exc:
            print("Error: {}".format(exc), file=sys.stderr)
            return 2
        print(redacted_configuration(cfg, components))
        return 0

    try:
        if args.tailor:
            run_tailor(args, cfg)
            return

        if args.seed:
            return run_seed(cfg)

        if args.list:
            return run_list(cfg)

        return run_daily(cfg, test=args.test)
    except ConfigurationError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
