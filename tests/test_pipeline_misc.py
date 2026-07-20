"""Characterization tests for pipeline notification helpers and URL fetch."""
import urllib.request

# --- modules under test (repoint on migration) ---
from job_search.config import MIN_JOB_TEXT_LEN, PipelineConfig
from job_search.pipeline import run as run_mod
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


def test_clean_job_description_preserves_plain_text_angle_brackets():
    # Non-HTML descriptions must keep literal angle-bracket content.
    assert clean_job_description("Map<String, Int> and Flow<T>") == "Map<String, Int> and Flow<T>"
    assert clean_job_description("Apply at <https://apply.example.com>") == (
        "Apply at <https://apply.example.com>"
    )
    # Entity-only text is still decoded, and real closing tags are still stripped.
    assert clean_job_description("Swift &amp; UIKit") == "Swift & UIKit"
    assert clean_job_description("<main>Complete details</main>") == "Complete details"


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


def test_format_run_summary_reports_all_outcomes():
    stats = run_mod.RunStats(
        new_jobs=7,
        evaluated=5,
        non_fit=3,
        fits=2,
        deferred=1,
        evaluation_failed=1,
        preparation_failed=1,
        notification_sent=1,
        cv_sent=0,
        delivery_failed=1,
    )

    message = run_mod._format_run_summary(stats)

    assert "New candidates: 7" in message
    assert "Evaluated: 5 (non-fit: 3, fit: 2)" in message
    assert "Deferred: 1" in message
    assert "Fit notifications sent: 1" in message
    assert "Verified CVs delivered: 0" in message
    assert "Evaluation failures: 1" in message
    assert "Preparation failures: 1" in message
    assert "Delivery failures: 1" in message
    assert "No evaluated jobs matched" not in message
    assert "will retry" in message


def test_format_run_summary_zero_matches_adds_none_matched_notice():
    message = run_mod._format_run_summary(run_mod.RunStats(evaluated=2, non_fit=2))

    assert "No evaluated jobs matched your criteria" in message


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


# ── run_daily state-sync wiring (Part 4c) ─────────────────────────────────────
def _stub_daily(monkeypatch, calls):
    """Neutralize run_daily's external work, recording pull/fetch/push order."""
    def fake_fetch(*a, **k):
        calls.append("fetch")
        from job_search.sources.health import FetchReport, SourceHealth, SourceStatus
        return FetchReport((), (SourceHealth("fake", SourceStatus.SUCCESS, 0, 1),))

    monkeypatch.setattr(run_mod, "pull_state", lambda *a, **k: calls.append("pull"))
    monkeypatch.setattr(run_mod, "push_state", lambda *a, **k: calls.append("push"))
    monkeypatch.setattr(run_mod, "fetch_jobs_with_health", fake_fetch)
    monkeypatch.setattr(run_mod, "load_criteria", lambda: "criteria")
    monkeypatch.setattr(run_mod, "load_tailoring_instructions", lambda: "instr")
    monkeypatch.setattr(run_mod, "load_base_tex", lambda: "tex")
    monkeypatch.setattr(run_mod, "load_seen_jobs", lambda: set())
    monkeypatch.setattr(run_mod, "save_seen_jobs", lambda *a, **k: None)

    class FakeLLM:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def usage_summary(self):
            return ""

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def send_message(self, *a, **k):
            pass

    monkeypatch.setattr(run_mod, "LLMClient", FakeLLM)
    monkeypatch.setattr(run_mod, "TelegramClient", FakeTelegram)


def _daily_cfg(**overrides):
    base = dict(llm_primary_api_key="g", telegram_bot_token="t", telegram_chat_id="c")
    base.update(overrides)
    return PipelineConfig(**base)


def test_run_daily_state_sync_pulls_before_fetch_and_pushes_after(monkeypatch):
    calls = []
    _stub_daily(monkeypatch, calls)
    run_mod.run_daily(_daily_cfg(state_sync=True))
    assert calls == ["pull", "fetch", "push"]  # pull is load-bearing before fetch


def test_run_daily_test_mode_pulls_but_never_pushes(monkeypatch):
    calls = []
    _stub_daily(monkeypatch, calls)
    run_mod.run_daily(_daily_cfg(state_sync=True), test=True)
    assert "pull" in calls
    assert "push" not in calls


def test_run_daily_without_state_sync_neither_pulls_nor_pushes(monkeypatch):
    calls = []
    _stub_daily(monkeypatch, calls)
    run_mod.run_daily(_daily_cfg(state_sync=False))
    assert calls == ["fetch"]
