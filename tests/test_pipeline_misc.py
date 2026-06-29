"""Characterization tests for pipeline notification helpers and URL fetch."""
import urllib.request

# --- modules under test (repoint on migration) ---
from job_search.config import MIN_JOB_TEXT_LEN
from job_search.pipeline.stages import _company_slug, _format_notification, fetch_job_text_from_url


def test_company_slug():
    assert _company_slug("Acme, Inc!") == "acme_inc"
    assert _company_slug("  ") == "unknown"
    assert _company_slug("") == "unknown"


def test_format_notification_basic():
    msg = _format_notification(
        {"title": "iOS Engineer", "company": "Acme", "location": "Berlin", "url": "https://x/1", "source": "remotive"},
        {"reason": "Strong match", "timezone_note": None},
    )
    assert "<b>iOS Engineer</b>" in msg
    assert "Acme" in msg
    assert "Berlin" in msg
    assert 'href="https://x/1"' in msg
    assert "Strong match" in msg
    assert "Timezone" not in msg


def test_format_notification_with_timezone():
    msg = _format_notification(
        {"title": "iOS", "company": "Acme", "location": "", "url": "u", "source": "s"},
        {"reason": "ok", "timezone_note": "US hours only"},
    )
    assert "Timezone" in msg
    assert "US hours only" in msg


def test_min_job_text_len_constant():
    assert MIN_JOB_TEXT_LEN == 200


def test_fetch_job_text_strips_scripts(monkeypatch, fake_http_response):
    html = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><script>var a = 1;</script><p>Real job text here</p></body></html>"
    )

    def fake_urlopen(req, timeout=None):
        return fake_http_response(html, headers={"content-type": "text/html; charset=utf-8"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    text = fetch_job_text_from_url("https://x/job")
    assert "Real job text here" in text
    assert "var a = 1" not in text
    assert "color:red" not in text


def test_fetch_job_text_returns_empty_on_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("blocked")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_job_text_from_url("https://x/job") == ""
