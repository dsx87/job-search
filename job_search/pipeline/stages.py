"""Per-job pipeline helpers: description fetching, formatting, delivery.

Telegram I/O goes through an injected TelegramClient (the `telegram` param)
rather than module globals, so a single client is built once and threaded
through. Tailoring and compilation live behind ``components.CVRenderer``; what
remains here is the presentation and the one Telegram send (``send_fit``) that
``DefaultOutputBackend`` delegates to.
"""
import html
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import MutableMapping

from ..components import DeliveryOutcome
from ..config import CV_FILENAME_PREFIX, MIN_JOB_TEXT_LEN
from ..http import read_capped
from ..models import Job, coerce_job
from ..text import collapse_ws, strip_html


class CVPreparationError(RuntimeError):
    """Raised when a verified one-page PDF cannot be prepared."""


class CVDeliveryError(RuntimeError):
    """Raised when both Telegram delivery steps do not complete."""

    def __init__(self, outcome: DeliveryOutcome):
        self.outcome = outcome
        detail = str(outcome.error) if outcome.error else "incomplete delivery"
        super().__init__(detail)


# Only run HTML stripping when the value actually looks like markup, so plain-text
# descriptions keep literal angle-bracket content (generics like ``Map<String, Int>``
# and markdown autolinks like ``<https://apply.example.com>``).
_HTML_MARKUP_RE = re.compile(
    r"</[a-z][^>]*>"                                   # any closing tag
    r"|<(?:div|p|br|span|ul|ol|li|table|tr|td|th|h[1-6]|strong|em|b|i|a|"
    r"section|article|header|footer|body|html|head|script|style|img|hr|"
    r"blockquote|pre|code)\b[^>]*>"                    # common opening tags
    r"|&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);",         # entities
    re.I,
)


def clean_job_description(value):
    """Return collapsed plain text, stripping HTML only when markup is present."""
    text = str(value or "")
    if _HTML_MARKUP_RE.search(text):
        return collapse_ws(strip_html(text))
    return collapse_ws(text)


def _is_http_job_url(url):
    parsed = urllib.parse.urlparse(str(url or "").strip())
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def ensure_job_description(job, fetcher=None, min_length=MIN_JOB_TEXT_LEN):
    """Keep the richest cleaned description and report whether it is sufficient."""
    legacy = job if isinstance(job, MutableMapping) and not isinstance(job, Job) else None
    job = coerce_job(job)
    current = clean_job_description(job.description)
    if len(current) < min_length and _is_http_job_url(job.url):
        fetch = fetcher or fetch_job_text_from_url
        try:
            fetched = clean_job_description(fetch(job.url))
        except Exception as exc:
            print(
                f"    Job-description enrichment failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            fetched = ""
        if len(fetched) > len(current):
            current = fetched

    job.description = current
    if legacy is not None:
        legacy["description"] = current
    return len(current) >= min_length


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
            # Capped read: this follows arbitrary posting URLs, so an oversized
            # or chunk-streaming response would otherwise be an OOM-kill mid-run
            # on the Pi (finding N10). A truncated page still yields usable text.
            body = read_capped(resp).decode(charset, errors="replace")
    except Exception as exc:
        print(f"    URL fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ""

    # Drop the contents of script/style/head/noscript blocks first — strip_html
    # only removes tags, not the CSS/JS text between them, which would otherwise
    # flood the LLM prompt with markup noise.
    body = re.sub(r"(?is)<(script|style|head|noscript)\b.*?</\1>", " ", body)
    return clean_job_description(body)


def _format_deferred_notification(jobs, limit=10):
    """Build one bounded, HTML-safe Telegram summary for deferred jobs."""
    count = len(jobs)
    noun = "posting" if count == 1 else "postings"
    lines = [
        f"⚠️ <b>{count} new job {noun} deferred</b>",
        "Not enough job-description text for reliable AI evaluation. "
        "They will retry next run.",
        "",
    ]

    for job in jobs[:limit]:
        title = collapse_ws(job.get("title", ""))
        company = collapse_ws(job.get("company", ""))
        label = " — ".join(part for part in (title, company) if part) or "Unknown job"
        if len(label) > 180:
            label = label[:179] + "…"
        safe_label = html.escape(label)
        url = str(job.get("url", "") or "").strip()
        if _is_http_job_url(url):
            lines.append(f'• <a href="{html.escape(url, quote=True)}">{safe_label}</a>')
        else:
            lines.append(f"• {safe_label}")

    omitted = count - min(count, limit)
    if omitted:
        lines.append(f"• … and {omitted} more")

    return "\n".join(lines)


def _format_uncertain_notification(items, limit=10):
    """One bounded, HTML-safe Telegram summary for jobs needing human review.

    `items` is a list of (job, evaluation) pairs whose deterministic verdict was
    "uncertain" — the policy could not confidently decide, so they are surfaced
    for review rather than silently dropped (audit Finding 18).
    """
    count = len(items)
    noun = "posting" if count == 1 else "postings"
    lines = [
        f"🔍 <b>{count} job {noun} flagged for review</b>",
        "The policy could not confidently decide these. Review and use /tailor if a fit.",
        "",
    ]
    for job, evaluation in items[:limit]:
        job = coerce_job(job)
        title = collapse_ws(job.get("title", ""))
        company = collapse_ws(job.get("company", ""))
        label = " — ".join(part for part in (title, company) if part) or "Unknown job"
        if len(label) > 180:
            label = label[:179] + "…"
        safe_label = html.escape(label)
        reason = html.escape(collapse_ws(str((evaluation or {}).get("reason", "")))[:300])
        url = str(job.get("url", "") or "").strip()
        if _is_http_job_url(url):
            head = f'• <a href="{html.escape(url, quote=True)}">{safe_label}</a>'
        else:
            head = f"• {safe_label}"
        lines.append(head + (f"\n  <i>{reason}</i>" if reason else ""))
    omitted = count - min(count, limit)
    if omitted:
        lines.append(f"• … and {omitted} more")
    return "\n".join(lines)


def _format_notification(job: dict, evaluation: dict) -> str:
    job = coerce_job(job)
    title = html.escape(job.get("title", "Unknown Title"))
    company = html.escape(job.get("company", "Unknown Company"))
    location = html.escape(job.get("location", ""))
    url = html.escape(job.get("url", ""), quote=True)
    source = html.escape(job.get("source", ""))
    reason = html.escape(evaluation.get("reason", ""))
    timezone_note = evaluation.get("timezone_note")

    posting = f'<a href="{url}">View posting</a>' if url else ""
    source_label = f"Source: {source}" if source else ""
    link_line = "  |  ".join(part for part in (posting, source_label) if part)
    lines = [
        f"<b>{title}</b>",
        f"<b>{company}</b>" + (f" — {location}" if location else ""),
    ]
    if link_line:
        lines.append(link_line)
    lines.extend(("", f"<i>{reason}</i>"))
    if timezone_note:
        lines.append(f"\n⚠️ <b>Timezone:</b> {html.escape(timezone_note)}")

    return "\n".join(lines)


def send_fit(payload: dict, telegram, notification_already_sent=False) -> DeliveryOutcome:
    """
    Send a prepared fit to Telegram: the notification message, then the tailored
    verified PDF. Returns explicit partial-progress flags for orchestration.
    """
    title = payload["title"]
    company = payload["company"]
    artifact = payload.get("artifact")
    pdf_bytes = artifact.content if artifact is not None else payload.get("pdf_bytes")
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        return DeliveryOutcome(
            error=ValueError("verified PDF bytes are required for delivery"),
            notification_satisfied=notification_already_sent,
        )

    notification_sent = False
    if not notification_already_sent:
        try:
            telegram.send_message(payload["message"])
        except Exception as exc:
            return DeliveryOutcome(error=exc)
        notification_sent = True

    slug = _company_slug(company)
    try:
        telegram.send_document(
            artifact.filename if artifact is not None else f"{CV_FILENAME_PREFIX}_{slug}.pdf",
            pdf_bytes,
            caption=f"Tailored CV — {title} at {company}",
        )
    except Exception as exc:
        return DeliveryOutcome(
            notification_sent=notification_sent,
            notification_satisfied=True,
            error=exc,
        )
    return DeliveryOutcome(
        notification_sent=notification_sent,
        notification_satisfied=True,
        cv_sent=True,
    )
