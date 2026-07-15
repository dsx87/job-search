"""Characterization tests for pipeline notification helpers and URL fetch."""
import urllib.request

# --- modules under test (repoint on migration) ---
from job_search.config import MIN_JOB_TEXT_LEN
from job_search.pipeline.stages import (
    _company_slug,
    _format_deferred_notification,
    _format_notification,
    clean_job_description,
    ensure_job_description,
    fetch_job_text_from_url,
)


def test_clean_job_description():
    assert clean_job_description("<p>Swift &amp; UIKit</p>\n<p>Remote</p>") == (
        "Swift & UIKit Remote"
    )


def test_description_length_boundary():
    assert ensure_job_description({"description": "x" * 200, "url": ""}) is True
    assert ensure_job_description({"description": "x" * 199, "url": ""}) is False


def test_sufficient_description_does_not_fetch():
    def unexpected_fetch(_url):
        raise AssertionError("fetch must not run")

    job = {"description": "x" * 200, "url": "https://example.com/job"}
    assert ensure_job_description(job, fetcher=unexpected_fetch) is True


def test_short_description_is_enriched():
    job = {"description": "<p>Swift</p>", "url": "https://example.com/job"}
    fetched = "<main>{}</main>".format("Complete iOS requirements " * 20)

    assert ensure_job_description(job, fetcher=lambda _url: fetched) is True
    assert "<main>" not in job["description"]
    assert len(job["description"]) >= 200


def test_shorter_fetched_text_does_not_replace_source():
    job = {"description": "x" * 150, "url": "https://example.com/job"}
    assert ensure_job_description(job, fetcher=lambda _url: "short") is False
    assert job["description"] == "x" * 150


def test_non_http_job_urls_do_not_fetch():
    calls = []

    for url in ("", "/relative", "mailto:jobs@example.com", "file:///tmp/job"):
        job = {"description": "short", "url": url}
        assert ensure_job_description(job, fetcher=calls.append) is False

    assert calls == []


def test_format_deferred_notification_is_bounded_and_html_safe():
    jobs = [
        {
            "title": "iOS <Lead>",
            "company": "A & B",
            "url": "https://example.com/job?a=1&b=2",
        },
        {"title": "Plain role", "company": "No Link", "url": "file:///job"},
    ] + [
        {"title": f"Role {index}", "company": "Acme", "url": f"https://x/{index}"}
        for index in range(10)
    ]

    message = _format_deferred_notification(jobs)

    assert "iOS &lt;Lead&gt; — A &amp; B" in message
    assert 'href="https://example.com/job?a=1&amp;b=2"' in message
    assert "• Plain role — No Link" in message
    assert 'href="file:///job"' not in message
    assert message.count("• ") == 11
    assert "• … and 2 more" in message


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
