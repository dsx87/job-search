"""Telegram Bot API delivery (stdlib only).

The low-level _tg_send_* functions take (bot_token, chat_id) explicitly; the
TelegramClient wraps a single (token, chat_id) so the pipeline stages can be
handed one client instead of reading module globals.

Two reliability properties are enforced here, at the client boundary, rather
than at each of the many call sites that build a message:

* **Every message fits Telegram's 4096-character cap** (finding N6). Overlong
  messages 400 and raise, and some callers have already marked their jobs seen
  by then — the review section in the legacy path was lost permanently that way.
  Truncation is HTML-aware, because a cut inside a tag or entity would 400 too.
* **Sends retry with backoff** (finding N8). In digest mode a single transient
  blip on the one ZIP send marks *every* fit failed, defers them a day, and
  re-pays the LLM + pdflatex cost next run. The LLM calls have had retries all
  along; delivery — the more expensive thing to lose — had none.
"""
import json
import re
import time
import urllib.error
import urllib.request

from ..config import (
    RETRYABLE_STATUS,
    TELEGRAM_MAX_MESSAGE_CHARS,
    TELEGRAM_RETRY_AFTER_CAP,
    TELEGRAM_RETRY_BACKOFF,
)

_TRUNCATION_SUFFIX = "\n… (truncated)"

# The subset of HTML Telegram accepts that this project actually emits. Anything
# else is escaped by the callers, so it never needs closing.
_CLOSEABLE_TAGS = ("b", "i", "u", "s", "code", "pre", "a", "blockquote")


def _open_tags(text):
    """Tags still open at the end of ``text``, outermost first."""
    stack = []
    index = 0
    while True:
        start = text.find("<", index)
        if start == -1:
            break
        end = text.find(">", start)
        if end == -1:
            break
        body = text[start + 1:end].strip()
        index = end + 1
        if body.startswith("/"):
            name = body[1:].strip().lower()
            if name in stack:
                # Drop the innermost matching open tag (and anything nested in it
                # that was left unclosed).
                del stack[stack.index(name):]
        else:
            name = body.split()[0].lower() if body else ""
            if name in _CLOSEABLE_TAGS:
                stack.append(name)
    return stack


def _safe_cut(text, budget):
    """Truncate to at most ``budget`` chars without splitting a tag or an entity."""
    if budget <= 0:
        return ""
    head = text[:budget]
    # Never end inside a tag or an entity: back off to before the opener.
    last_open = head.rfind("<")
    if last_open > head.rfind(">"):
        head = head[:last_open]
    last_amp = head.rfind("&")
    if last_amp > head.rfind(";") and len(head) - last_amp <= 10:
        head = head[:last_amp]
    # Prefer a line break, but only if it doesn't cost most of the message.
    newline = head.rfind("\n")
    if newline >= budget // 2:
        head = head[:newline]
    return head.rstrip()


def bound_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    """Return ``text`` trimmed to ``limit`` characters, keeping the HTML valid.

    Cuts on a line boundary when one is available, never inside a tag or an HTML
    entity, and re-closes whatever tags the cut left open — an unbalanced or
    half-written tag is itself a 400 from Telegram, so a naive slice would just
    trade one failure for another. The closing tags count against the limit, and
    cutting can itself change which tags are open, so the budget is re-solved a
    few times; if it somehow doesn't settle, the tags are stripped instead, which
    is always both valid and shorter.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text

    room = max(0, limit - len(_TRUNCATION_SUFFIX))
    budget = room
    for _attempt in range(8):
        head = _safe_cut(text, budget)
        closers = "".join(f"</{tag}>" for tag in reversed(_open_tags(head)))
        if len(head) + len(closers) <= room:
            return head + closers + _TRUNCATION_SUFFIX
        budget = room - len(closers)
    plain = re.sub(r"<[^>]*>", "", text)[:room].rstrip()
    return plain + _TRUNCATION_SUFFIX


def _retry_after(exc) -> float:
    """Telegram's own requested wait for a 429, in seconds, or 0 when absent."""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        wait = float((body.get("parameters") or {}).get("retry_after", 0))
    except Exception:
        return 0.0
    return wait if 0 < wait <= TELEGRAM_RETRY_AFTER_CAP else 0.0


def _send_with_retry(send, label: str) -> None:
    """Call ``send()``, retrying transient failures on the shared backoff ladder.

    Same shape as ``llm.clients._post_json_with_retry``: retry the transient
    statuses and network errors, re-raise on the last attempt so the caller's
    failure handling still runs. A 400 (bad request — malformed HTML, oversized
    payload) is permanent and is never retried. ``HTTPError`` must be caught
    before ``URLError`` — it is a subclass.
    """
    delays = TELEGRAM_RETRY_BACKOFF
    attempts = len(delays)
    for attempt, delay in enumerate(delays, 1):
        try:
            send()
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise
            delay = _retry_after(exc) or delay
            print(
                f"    telegram {label} transient error {exc.code} — waiting "
                f"{delay:g}s (attempt {attempt}/{attempts})...",
                flush=True,
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise
            reason = getattr(exc, "reason", exc)
            print(
                f"    telegram {label} transient network error ({reason}) — waiting "
                f"{delay:g}s (attempt {attempt}/{attempts})...",
                flush=True,
            )
        time.sleep(delay)


def _tg_send_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": bound_message(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode()

    def send():
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()

    _send_with_retry(send, "sendMessage")


def _tg_send_document(bot_token: str, chat_id: str, filename: str, content: bytes, caption: str) -> None:
    boundary = "PipelineBoundary8a3f1d6e"
    crlf = b"\r\n"

    def part_field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        ).encode()

    body = (
        part_field("chat_id", chat_id)
        # Captions cap at 1024, not 4096, and are sent as plain text here.
        + part_field("caption", bound_message(caption, 1024))
        + (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n"
            f"\r\n"
        ).encode()
        + content
        + crlf
        + f"--{boundary}--\r\n".encode()
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

    def send():
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()

    _send_with_retry(send, "sendDocument")


class TelegramClient:
    """A (token, chat_id)-bound view over the send functions, built once in
    run.py / cli.py and injected into the pipeline stages."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str) -> None:
        _tg_send_message(self.bot_token, self.chat_id, text)

    def send_document(self, filename: str, content: bytes, caption: str) -> None:
        _tg_send_document(self.bot_token, self.chat_id, filename, content, caption)
