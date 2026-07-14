"""Characterization tests for pipeline notification helpers and URL fetch."""
import urllib.request

# --- modules under test (repoint on migration) ---
from job_search.config import MIN_JOB_TEXT_LEN, PipelineConfig
from job_search.pipeline import run as run_mod
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


# ── run_daily state-sync wiring (Part 4c) ─────────────────────────────────────
def _stub_daily(monkeypatch, calls):
    """Neutralize run_daily's external work, recording pull/fetch/push order."""
    def fake_fetch(*a, **k):
        calls.append("fetch")
        return []

    monkeypatch.setattr(run_mod, "pull_state", lambda *a, **k: calls.append("pull"))
    monkeypatch.setattr(run_mod, "push_state", lambda *a, **k: calls.append("push"))
    monkeypatch.setattr(run_mod, "fetch_jobs", fake_fetch)
    monkeypatch.setattr(run_mod, "load_criteria", lambda: "criteria")
    monkeypatch.setattr(run_mod, "load_tailoring_instructions", lambda: "instr")
    monkeypatch.setattr(run_mod, "load_base_tex", lambda: "tex")
    monkeypatch.setattr(run_mod, "load_seen_jobs", lambda: set())
    monkeypatch.setattr(run_mod, "save_seen_jobs", lambda *a, **k: None)

    class FakeLLM:
        def __init__(self, *a, **k):
            pass

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
    base = dict(gemini_api_key="g", telegram_bot_token="t", telegram_chat_id="c")
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
