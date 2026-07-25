"""Delivery-boundary tests: the 4096-char cap (N6) and send retries (N8)."""
import io
import json
import re
import urllib.error

import pytest

# --- modules under test (repoint on migration) ---
from job_search.config import TELEGRAM_MAX_MESSAGE_CHARS
from job_search.notify import telegram as tg
from job_search.pipeline.stages import _format_uncertain_notification


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"ok": true}'


def _http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Keep the backoff ladder instant; record what it would have waited."""
    waits = []
    monkeypatch.setattr(tg.time, "sleep", lambda seconds: waits.append(seconds))
    return waits


def _tag_names(html):
    return re.findall(r"<(/?)([a-z]+)[^>]*>", html)


def _tags_balanced(html):
    stack = []
    for closing, name in _tag_names(html):
        if closing:
            assert stack and stack[-1] == name, f"unbalanced </{name}> in {html!r}"
            stack.pop()
        elif name in tg._CLOSEABLE_TAGS:
            stack.append(name)
    return not stack


# ── the 4096-char cap ─────────────────────────────────────────────────────────
def test_short_message_passes_through_untouched():
    text = "<b>Fine</b>\nnothing to trim"
    assert tg.bound_message(text) == text


def test_long_message_is_trimmed_to_the_cap_and_says_so():
    text = "\n".join(f"• line {i} " + "x" * 80 for i in range(200))
    bounded = tg.bound_message(text)
    assert len(bounded) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert bounded.endswith("… (truncated)")
    assert bounded.startswith("• line 0")


def test_truncation_closes_tags_left_open():
    text = "<b>" + "y" * 6000 + "</b>"
    bounded = tg.bound_message(text)
    assert len(bounded) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert bounded.endswith("</b>\n… (truncated)")
    assert _tags_balanced(bounded)


def test_truncation_closes_nested_tags_innermost_first():
    text = "<b>bold <i>italic " + "z" * 6000 + "</i></b>"
    bounded = tg.bound_message(text)
    assert bounded.endswith("</i></b>\n… (truncated)")
    assert _tags_balanced(bounded)


def test_truncation_never_cuts_inside_a_tag():
    # An exact slice would land in the middle of the anchor, which Telegram 400s.
    text = "q" * (TELEGRAM_MAX_MESSAGE_CHARS - 20) + '<a href="https://example.com/very/long">label</a>'
    bounded = tg.bound_message(text)
    assert "<a href" not in bounded or bounded.count("</a>") == 1
    assert _tags_balanced(bounded)


def test_truncation_never_cuts_inside_an_entity():
    text = "w" * (TELEGRAM_MAX_MESSAGE_CHARS - 20) + "&amp;" * 20
    bounded = tg.bound_message(text)
    body = bounded[: -len("\n… (truncated)")]
    assert not re.search(r"&[a-z]*$", body)


def test_measured_uncertain_notification_now_fits(monkeypatch):
    # finding N6's measured case: 10 items x (180-char label + 300-char reason)
    # produced ~4,800 chars, so sendMessage 400'd and raised — and in the legacy
    # path those jobs had ALREADY been marked seen, so the review list was lost
    # for good.
    items = [
        (
            {"title": "Senior iOS Engineer " + "T" * 150, "company": "C" * 40,
             "url": f"https://example.com/{i}"},
            {"reason": "R" * 400},
        )
        for i in range(10)
    ]
    raw = _format_uncertain_notification(items)
    assert len(raw) > TELEGRAM_MAX_MESSAGE_CHARS  # the bug reproduces at the formatter

    sent = {}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: sent.update(payload=json.loads(request.data)) or _Response(),
    )
    tg._tg_send_message("token", "chat", raw)

    assert len(sent["payload"]["text"]) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert _tags_balanced(sent["payload"]["text"])


def test_every_run_message_formatter_stays_within_the_cap():
    # N6 also named _format_run_summary (10 failure details + health text) and
    # _send_error_notification (an unbounded {exc}, e.g. a whole pdflatex log).
    # Bounding at the client covers all three, including ones added later.
    from job_search.pipeline.run import RunStats, _format_run_summary

    stats = RunStats(
        fits=10,
        failure_details=[f"• {'D' * 400}" for _ in range(10)],
    )
    long_summary = _format_run_summary(stats, source_warning="W" * 3000)
    assert len(long_summary) > TELEGRAM_MAX_MESSAGE_CHARS
    assert len(tg.bound_message(long_summary)) <= TELEGRAM_MAX_MESSAGE_CHARS

    huge_error = "<code>RuntimeError: " + "L" * 20000 + "</code>"
    bounded = tg.bound_message(huge_error)
    assert len(bounded) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert _tags_balanced(bounded)


# ── send retries ──────────────────────────────────────────────────────────────
def test_message_retries_a_transient_error_then_succeeds(_no_sleeping, monkeypatch):
    attempts = []

    def urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise _http_error(503)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_message("token", "chat", "hello")

    assert len(attempts) == 2
    assert _no_sleeping == [2]


def test_message_retries_a_network_error(_no_sleeping, monkeypatch):
    attempts = []

    def urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("connection reset")
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_message("token", "chat", "hello")
    assert len(attempts) == 3


def test_message_honors_telegram_retry_after_on_429(_no_sleeping, monkeypatch):
    body = json.dumps({"parameters": {"retry_after": 17}}).encode()
    attempts = []

    def urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise _http_error(429, body)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_message("token", "chat", "hello")
    assert _no_sleeping == [17]


def test_absurd_retry_after_falls_back_to_the_fixed_ladder(_no_sleeping, monkeypatch):
    body = json.dumps({"parameters": {"retry_after": 99999}}).encode()
    attempts = []

    def urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise _http_error(429, body)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_message("token", "chat", "hello")
    assert _no_sleeping == [2]


def test_permanent_error_is_not_retried(_no_sleeping, monkeypatch):
    attempts = []

    def urlopen(request, timeout):
        attempts.append(1)
        raise _http_error(400)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        tg._tg_send_message("token", "chat", "hello")
    assert len(attempts) == 1
    assert _no_sleeping == []


def test_message_reraises_after_exhausting_attempts(_no_sleeping, monkeypatch):
    def urlopen(request, timeout):
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        tg._tg_send_message("token", "chat", "hello")


def test_document_send_retries_too(_no_sleeping, monkeypatch):
    # In digest mode this ONE send is the entire run's delivery: a transient blip
    # defers every fit for a day and re-pays the LLM + pdflatex cost (N8).
    attempts = []

    def urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise _http_error(502)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_document("token", "chat", "d.zip", b"PK\x03\x04", "caption")

    assert len(attempts) == 2
    assert attempts[0].endswith("/sendDocument")


def test_document_caption_is_bounded_to_the_caption_cap(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["body"] = request.data
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    tg._tg_send_document("token", "chat", "d.zip", b"PK", "c" * 5000)

    body = captured["body"].decode("utf-8", "replace")
    caption = body.split('name="caption"')[1].split("\r\n\r\n")[1].split("\r\n--")[0]
    assert len(caption) <= 1024
