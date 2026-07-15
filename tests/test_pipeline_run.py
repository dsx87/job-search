"""Regressions for description gating in the scheduled pipeline."""
from types import SimpleNamespace

from job_search.models import Job
from job_search.pipeline import run
from job_search.pipeline import stages


class FakeLLM:
    def usage_summary(self):
        return "usage"


class FakeTelegram:
    def __init__(self, fail_deferred=False):
        self.messages = []
        self.documents = []
        self.fail_deferred = fail_deferred

    def send_message(self, message):
        if self.fail_deferred and "deferred" in message:
            raise RuntimeError("telegram down")
        self.messages.append(message)

    def send_document(self, filename, content, caption):
        self.documents.append((filename, content, caption))


def make_config():
    return SimpleNamespace(
        gemini_api_key="gemini",
        qwen_api_key="qwen",
        telegram_bot_token="token",
        telegram_chat_id="chat",
        eval_workers=2,
        tailor_workers=1,
    )


def install_daily_fakes(monkeypatch, jobs, telegram=None):
    telegram = telegram or FakeTelegram()
    saved = []
    monkeypatch.setattr(run, "TelegramClient", lambda *_args: telegram)
    monkeypatch.setattr(run, "LLMClient", lambda *_args: FakeLLM())
    monkeypatch.setattr(run, "load_criteria", lambda: "criteria")
    monkeypatch.setattr(run, "load_tailoring_instructions", lambda: "instructions")
    monkeypatch.setattr(run, "load_base_tex", lambda: "base")
    monkeypatch.setattr(run, "fetch_jobs", lambda verbose=True: jobs)
    monkeypatch.setattr(run, "load_seen_jobs", lambda: set())
    monkeypatch.setattr(run, "save_seen_jobs", lambda seen: saved.append(set(seen)))
    return telegram, saved


def test_all_deferred_stays_unseen_and_does_not_claim_none_matched(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))

    run.run_daily(make_config())

    assert all("https://x/short" not in state for state in saved)
    assert all("short|acme" not in state for state in saved)
    assert len(telegram.messages) == 1
    assert "1 new job posting deferred" in telegram.messages[0]
    assert all("none matched" not in message for message in telegram.messages)


def test_mixed_run_evaluates_only_sufficient_job(monkeypatch):
    short = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    complete = Job(title="Complete", company="Beta", url="https://x/complete", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [short, complete])
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    evaluated = []

    def fake_evaluate(_client, _criteria, job):
        evaluated.append(job["title"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", fake_evaluate)
    run.run_daily(make_config())

    assert evaluated == ["Complete"]
    assert "https://x/complete" in saved[-1]
    assert "https://x/short" not in saved[-1]
    assert any("Short" in message and "deferred" in message for message in telegram.messages)
    assert any("1 new posting evaluated" in message for message in telegram.messages)


def test_successful_enrichment_is_cleaned_before_evaluation(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    _telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(
        stages,
        "fetch_job_text_from_url",
        lambda _url: "<main>{}</main>".format("Complete requirements &amp; details " * 10),
    )
    descriptions = []

    def fake_evaluate(_client, _criteria, candidate):
        descriptions.append(candidate["description"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", fake_evaluate)
    run.run_daily(make_config())

    assert len(descriptions[0]) >= 200
    assert "<main>" not in descriptions[0]
    assert "&amp;" not in descriptions[0]
    assert "https://x/short" in saved[-1]


def test_deferred_notice_failure_is_soft_and_job_stays_unseen(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    _telegram, saved = install_daily_fakes(monkeypatch, [job], FakeTelegram(fail_deferred=True))
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))

    run.run_daily(make_config())

    assert "https://x/short" not in saved[-1]


def test_mode_defers_before_process_job_without_seen_state(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    monkeypatch.setattr(run, "process_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no processing")))
    monkeypatch.setattr(run, "load_seen_jobs", lambda: (_ for _ in ()).throw(AssertionError("no load")))
    monkeypatch.setattr(run, "save_seen_jobs", lambda _seen: (_ for _ in ()).throw(AssertionError("no save")))

    run.run_daily(make_config(), test=True)

    assert len(telegram.messages) == 1
    assert "1 new job posting deferred" in telegram.messages[0]
