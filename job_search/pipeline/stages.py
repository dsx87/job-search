"""Per-job pipeline stages: evaluate → tailor → compile → deliver.

Telegram I/O goes through an injected TelegramClient (the `telegram` param)
rather than module globals, so a single client is built once in run.py/cli.py
and threaded through. prepare_fit performs NO Telegram I/O (safe to run
concurrently); send_fit does the delivery.
"""
import re
import sys
import urllib.request

from ..config import MIN_JOB_TEXT_LEN, load_base_tex, load_tailoring_instructions
from ..latex.compile import compile_with_fixes
from ..llm.eval import evaluate_job
from ..llm.tailor import tailor_resume
from ..text import strip_html


def _company_slug(company: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_")
    return slug or "unknown"


def fetch_job_text_from_url(url: str) -> str:
    """Best-effort fetch of a job posting's text from a URL.

    Returns plain text (HTML stripped, whitespace collapsed), or "" on any
    failure (HTTP error, timeout, empty body). The caller decides whether the
    result is usable or whether the user must fall back to pasting the text;
    many job boards block CI IPs or require JS, so an empty return is expected.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
    except Exception as exc:
        print(f"    URL fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ""

    # Drop the contents of script/style/head/noscript blocks first — strip_html
    # only removes tags, not the CSS/JS text between them, which would otherwise
    # flood the LLM prompt with markup noise.
    body = re.sub(r"(?is)<(script|style|head|noscript)\b.*?</\1>", " ", body)
    text = strip_html(body)
    return " ".join(text.split())


def _format_notification(job: dict, evaluation: dict) -> str:
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("location", "")
    url = job.get("url", "")
    source = job.get("source", "")
    reason = evaluation.get("reason", "")
    timezone_note = evaluation.get("timezone_note")

    lines = [
        f"<b>{title}</b>",
        f"<b>{company}</b>" + (f" — {location}" if location else ""),
        f'<a href="{url}">View posting</a>  |  Source: {source}',
        "",
        f"<i>{reason}</i>",
    ]
    if timezone_note:
        lines.append(f"\n⚠️ <b>Timezone:</b> {timezone_note}")

    return "\n".join(lines)


def prepare_fit(gemini, tailoring_instructions: str, base_tex: str, job: dict, evaluation: dict) -> dict:
    """
    Tailor + compile the CV for a job already judged a fit, and assemble the
    Telegram send-payload. Performs NO Telegram I/O — pure preparation, so it is
    safe to run concurrently across jobs. Tailoring/compilation failures are soft:
    we fall back to raw .tex or no document.

    Returns a payload dict consumed by send_fit().
    """
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"    Fit! Tailoring resume: {title} at {company}", flush=True)

    tex_source = None
    try:
        tex_source = tailor_resume(gemini, tailoring_instructions, base_tex, job)
    except Exception as exc:
        print(f"    Tailoring error: {exc}", file=sys.stderr)

    pdf_bytes = None
    final_tex = tex_source
    compilation_failed = False
    if tex_source:
        print(f"    Compiling PDF: {title} at {company}...", flush=True)
        ok, pdf_bytes, final_tex = compile_with_fixes(gemini, tex_source)
        if ok:
            print(f"    PDF compiled successfully: {title} at {company}.", flush=True)
        else:
            compilation_failed = True
            print(f"    PDF compilation failed after all attempts ({title} at {company}) — will send .tex as fallback.", flush=True)

    message = _format_notification(job, evaluation)
    if compilation_failed:
        message += "\n\n⚠️ <b>Note:</b> PDF compilation failed — raw LaTeX attached instead."

    return {
        "title": title,
        "company": company,
        "message": message,
        "pdf_bytes": pdf_bytes,
        "final_tex": final_tex,
    }


def send_fit(payload: dict, telegram) -> None:
    """
    Send a prepared fit to Telegram: the notification message, then the tailored
    CV document (PDF, or .tex fallback). Raises if the message send fails so the
    caller can leave the job unseen for a retry next run; document-send failures
    are soft (logged, not raised).
    """
    title = payload["title"]
    company = payload["company"]

    # Let Telegram message errors propagate — caller will not mark the job as seen.
    telegram.send_message(payload["message"])

    slug = _company_slug(company)
    pdf_bytes = payload.get("pdf_bytes")
    final_tex = payload.get("final_tex")
    if pdf_bytes:
        try:
            telegram.send_document(
                f"igor_pivnyk_cv_{slug}.pdf", pdf_bytes,
                caption=f"Tailored CV — {title} at {company}",
            )
        except Exception as exc:
            print(f"    Telegram PDF error: {exc}", file=sys.stderr)
    elif final_tex:
        try:
            telegram.send_document(
                f"igor_pivnyk_cv_{slug}.tex", final_tex.encode("utf-8"),
                caption=f"Tailored CV (LaTeX source) — {title} at {company}",
            )
        except Exception as exc:
            print(f"    Telegram document error: {exc}", file=sys.stderr)


def process_job(gemini, criteria: str, tailoring_instructions: str, base_tex: str, job: dict, telegram) -> bool:
    """
    Evaluate → tailor → compile → send a single job end-to-end.
    Returns True if a notification was sent (job was a fit).
    Raises on errors that warrant a retry next run (evaluation failure, Telegram send failure).
    Tailoring/compilation failures are soft — we fall back to no CV or raw .tex.

    Thin wrapper over evaluate_job/prepare_fit/send_fit, kept for --test mode.
    """
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"  Evaluating: {title} at {company}", flush=True)

    # Let evaluation errors propagate — caller will not mark the job as seen.
    evaluation = evaluate_job(gemini, criteria, job)

    if not evaluation.get("fit"):
        print(f"    Skip — {evaluation.get('reason', '')}")
        return False

    payload = prepare_fit(gemini, tailoring_instructions, base_tex, job, evaluation)
    send_fit(payload, telegram)
    return True


def tailor_single_job(client, job: dict, telegram) -> None:
    """Tailor + compile + Telegram-deliver a CV for one manually supplied job.

    Reuses the same tailoring/compilation/delivery path as the scheduled
    pipeline (see process_job). Does NOT touch seen_jobs.json — this is an
    on-demand action, not part of dedup state.
    """
    title = job.get("title", "iOS Developer")
    company = job.get("company", "the role")

    print(f"  Tailoring CV for: {title} at {company}", flush=True)
    tex_source = tailor_resume(client, load_tailoring_instructions(), load_base_tex(), job)

    print("  Compiling PDF...", flush=True)
    ok, pdf_bytes, final_tex = compile_with_fixes(client, tex_source)

    header = (
        f"<b>{title}</b>\n"
        f"<b>{company}</b>" + (f" — {job['location']}" if job.get("location") else "") + "\n"
        + (f'<a href="{job["url"]}">View posting</a>\n' if job.get("url") else "")
        + "\n📄 Tailored CV attached."
    )
    if not ok:
        header += "\n\n⚠️ <b>Note:</b> PDF compilation failed — raw LaTeX attached instead."
    telegram.send_message(header)

    slug = _company_slug(company)
    if pdf_bytes:
        telegram.send_document(
            f"igor_pivnyk_cv_{slug}.pdf", pdf_bytes,
            caption=f"Tailored CV — {title} at {company}",
        )
        print("  PDF sent to Telegram.", flush=True)
    else:
        telegram.send_document(
            f"igor_pivnyk_cv_{slug}.tex", final_tex.encode("utf-8"),
            caption=f"Tailored CV (LaTeX source) — {title} at {company}",
        )
        print("  LaTeX source sent to Telegram (compilation failed).", flush=True)


def _send_error_notification(exc: Exception, telegram) -> None:
    try:
        text = f"⚠️ <b>Pipeline error</b>\n\n<code>{type(exc).__name__}: {exc}</code>"
        telegram.send_message(text)
    except Exception:
        pass
