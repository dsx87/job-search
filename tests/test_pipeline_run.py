"""Regressions for description gating in the scheduled pipeline."""
import datetime
import io
import zipfile
from types import SimpleNamespace

import pyzipper
import pytest

from job_search.models import Job
from job_search.pipeline import run
from job_search.pipeline import stages
from job_search.pipeline.stages import DeliveryOutcome
from job_search.sources.health import FetchReport, SourceHealth, SourceStatus
from job_search.state.seen_jobs import (
    delivery_identity_tokens,
    mark_delivery_notified,
    record_delivery_failure,
)


class FakeLLM:
    def usage_summary(self):
        return "usage"


class FakeTelegram:
    def __init__(self, fail_deferred=False, fail_pending=False, fail_block=False):
        self.messages = []
        self.documents = []
        self.fail_deferred = fail_deferred
        self.fail_pending = fail_pending
        self.fail_block = fail_block

    def send_message(self, message):
        if self.fail_deferred and "deferred" in message:
            raise RuntimeError("telegram down")
        if self.fail_pending and "verified CV pending" in message:
            raise RuntimeError("telegram down")
        if self.fail_block and "automated CV delivery blocked" in message:
            raise RuntimeError("telegram down")
        self.messages.append(message)

    def send_document(self, filename, content, caption):
        self.documents.append((filename, content, caption))


def make_config(digest_delivery=False, telegraph_access_token=""):
    # Existing tests exercise the legacy per-job delivery path, so this defaults
    # digest_delivery OFF; the digest-mode tests below pass digest_delivery=True.
    return SimpleNamespace(
        llm_primary_scheme="gemini",
        llm_primary_model="gemini-custom",
        llm_primary_api_key="primary-key",
        llm_primary_api_base="https://gemini.example/models",
        llm_fallback_scheme="openai",
        llm_fallback_model="gpt-custom",
        llm_fallback_api_key="fallback-key",
        llm_fallback_api_base="https://oai.example/v1",
        telegram_bot_token="token",
        telegram_chat_id="chat",
        eval_workers=2,
        tailor_workers=1,
        sources_enable=(),
        sources_disable=(),
        state_sync=False,
        digest_delivery=digest_delivery,
        telegraph_access_token=telegraph_access_token,
        # Empty disables sections; the two tests below opt in with a tmp_path file.
        sections_file="",
    )


def install_daily_fakes(monkeypatch, jobs, telegram=None, initial_seen=None, llm_calls=None):
    telegram = telegram or FakeTelegram()
    state = set(initial_seen or ())
    saved = []

    def save(seen):
        state.clear()
        state.update(seen)
        saved.append(set(state))

    monkeypatch.setattr(run, "TelegramClient", lambda *_args: telegram)

    class FakeLLMClient:
        @staticmethod
        def from_config(cfg):
            if llm_calls is not None:
                llm_calls.append(cfg)
            return FakeLLM()

    monkeypatch.setattr(run, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(run, "load_criteria", lambda: "criteria")
    monkeypatch.setattr(run, "load_tailoring_instructions", lambda: "instructions")
    monkeypatch.setattr(run, "load_base_tex", lambda: "base")
    monkeypatch.setattr(
        run,
        "fetch_jobs_with_health",
        lambda **_kwargs: FetchReport(
            tuple(jobs), (SourceHealth("fake", SourceStatus.SUCCESS, len(jobs), 1),),
        ),
    )
    monkeypatch.setattr(run, "load_seen_jobs", lambda: set(state))
    monkeypatch.setattr(run, "save_seen_jobs", save)
    return telegram, saved


def test_total_source_outage_aborts_without_evaluation_or_state_save(monkeypatch):
    telegram, saved = install_daily_fakes(monkeypatch, [])
    monkeypatch.setattr(
        run,
        "fetch_jobs_with_health",
        lambda **_kwargs: FetchReport((), (
            SourceHealth("down", SourceStatus.FAILED, failure_detail="offline"),
        )),
    )
    monkeypatch.setattr(run, "load_seen_jobs", lambda: (_ for _ in ()).throw(AssertionError("no state load")))

    assert run.run_daily(make_config()) == 1
    assert saved == []
    assert any("source outage" in message.lower() for message in telegram.messages)


def test_partial_daily_run_continues_and_reports_unhealthy_source(monkeypatch):
    telegram, _saved = install_daily_fakes(monkeypatch, [])
    monkeypatch.setattr(
        run,
        "fetch_jobs_with_health",
        lambda **_kwargs: FetchReport((), (
            SourceHealth("healthy", SourceStatus.SUCCESS, 0, 1),
            SourceHealth("flaky", SourceStatus.PARTIAL, 0, 2, "page two failed"),
        )),
    )

    assert run.run_daily(make_config()) == 0
    assert any("flaky: partial" in message for message in telegram.messages)


def test_run_daily_forwards_provider_configuration(monkeypatch):
    llm_calls = []
    cfg = make_config()
    install_daily_fakes(monkeypatch, [], llm_calls=llm_calls)

    run.run_daily(cfg)

    # LLMClient.from_config is handed the whole config, unchanged.
    assert llm_calls == [cfg]
    assert llm_calls[0].llm_primary_model == "gemini-custom"
    assert llm_calls[0].llm_fallback_api_base == "https://oai.example/v1"


def test_deferred_markers_ignore_semantically_empty_job_identity():
    assert run._deferred_markers(Job()) == set()


def test_run_seed_does_not_persist_empty_identity_keys(monkeypatch):
    saved = []
    monkeypatch.setattr(
        run,
        "fetch_jobs_with_health",
        lambda **_kwargs: FetchReport(
            (Job(), Job(title="iOS", company="Acme")),
            (SourceHealth("fake", SourceStatus.SUCCESS, 2, 1),),
        ),
    )
    monkeypatch.setattr(run, "load_seen_jobs", lambda: set())
    monkeypatch.setattr(run, "save_seen_jobs", lambda seen: saved.append(set(seen)))

    run.run_seed(make_config())

    assert saved == [{"ios|acme"}]


def test_all_deferred_stays_unseen_and_summary_reports_zero_matches(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))

    run.run_daily(make_config())

    assert all("https://x/short" not in state for state in saved)
    assert all("short|acme" not in state for state in saved)
    assert len(telegram.messages) == 2
    assert "1 new job posting deferred" in telegram.messages[0]
    assert "Deferred: 1" in telegram.messages[-1]
    assert "No evaluated jobs matched your criteria" in telegram.messages[-1]


def test_mixed_run_evaluates_only_sufficient_job(monkeypatch):
    short = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    complete = Job(title="Complete", company="Beta", url="https://x/complete", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [short, complete])
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "")
    evaluated = []

    def fake_evaluate(_client, _criteria, job):
        assert isinstance(job, Job)
        evaluated.append(job["title"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", fake_evaluate)
    run.run_daily(make_config())

    assert evaluated == ["Complete"]
    assert "https://x/complete" in saved[-1]
    assert "https://x/short" not in saved[-1]
    assert any("Short" in message and "deferred" in message for message in telegram.messages)
    assert "Evaluated: 1 (non-fit: 1, fit: 0)" in telegram.messages[-1]


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
    assert "deferred:url:https://x/short" in saved[-1]
    assert "deferred:job:short|acme" in saved[-1]


def test_repeat_deferred_notification_is_suppressed_but_job_is_retried(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    attempts = []

    def insufficient(candidate):
        attempts.append(candidate["url"])
        return False

    monkeypatch.setattr(run, "ensure_job_description", insufficient)

    run.run_daily(make_config())
    run.run_daily(make_config())

    deferred_messages = [m for m in telegram.messages if "posting deferred" in m]
    assert attempts == ["https://x/short", "https://x/short"]
    assert len(deferred_messages) == 1
    assert "https://x/short" not in saved[-1]
    assert "short|acme" not in saved[-1]
    assert "deferred:url:https://x/short" in saved[-1]
    assert "deferred:job:short|acme" in saved[-1]


def test_previously_deferred_job_can_later_be_evaluated(monkeypatch):
    job = Job(title="Short", company="Acme", url="https://x/short", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    sufficiency = iter((False, True))
    evaluated = []

    monkeypatch.setattr(run, "ensure_job_description", lambda _job: next(sufficiency))
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda _client, _criteria, candidate: (
            evaluated.append(candidate["url"])
            or {"fit": False, "reason": "no", "timezone_note": None}
        ),
    )

    run.run_daily(make_config())
    run.run_daily(make_config())

    assert evaluated == ["https://x/short"]
    assert "https://x/short" in saved[-1]
    assert "short|acme" in saved[-1]
    assert len([m for m in telegram.messages if "posting deferred" in m]) == 1


def test_new_sparse_job_still_notifies_after_another_job_was_deferred(monkeypatch):
    jobs = [Job(title="First", company="Acme", url="https://x/first", description="tiny")]
    telegram, _saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: False)

    run.run_daily(make_config())
    jobs[:] = [Job(title="Second", company="Beta", url="https://x/second", description="tiny")]
    run.run_daily(make_config())

    deferred_messages = [m for m in telegram.messages if "posting deferred" in m]
    assert len(deferred_messages) == 2
    assert "First" in deferred_messages[0]
    assert "Second" in deferred_messages[1]


def test_all_evaluations_error_still_sends_completion_notice(monkeypatch):
    job = Job(title="Boom", company="Acme", url="https://x/boom", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("llm down")),
    )

    run.run_daily(make_config())

    # Not silent: an all-error run still delivers a completion notice.
    assert any("Job search complete" in m for m in telegram.messages)
    # The errored job stays unseen so it retries next run.
    assert "https://x/boom" not in saved[-1]
    assert "boom|acme" not in saved[-1]


def test_fit_that_fails_to_send_does_not_claim_none_matched(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: {"fit": True, "reason": "great", "timezone_note": None},
    )
    monkeypatch.setattr(run, "prepare_fit", lambda *_args: {"title": "Match"})
    monkeypatch.setattr(
        run,
        "send_fit",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("telegram down")),
    )

    run.run_daily(make_config())

    # A fit that merely failed to send is NOT "none matched".
    assert all("none matched" not in m for m in telegram.messages)
    # The unsent fit stays unseen so it retries next run.
    assert "https://x/match" not in saved[-1]


def _install_fit(monkeypatch, send_outcome=None, prepare_error=None):
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: {"fit": True, "reason": "great", "timezone_note": None},
    )
    if prepare_error is not None:
        monkeypatch.setattr(
            run,
            "prepare_fit",
            lambda *_args: (_ for _ in ()).throw(prepare_error),
        )
    else:
        monkeypatch.setattr(
            run,
            "prepare_fit",
            lambda *_args: {
                "title": "Match",
                "company": "Acme",
                "message": "fit",
                "pdf_bytes": b"PDF",
            },
        )
    if send_outcome is not None:
        monkeypatch.setattr(run, "send_fit", lambda *_args, **_kwargs: send_outcome)


def test_preparation_failure_remains_unseen_and_is_reported(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    _install_fit(monkeypatch, prepare_error=RuntimeError("compile down"))

    run.run_daily(make_config())

    assert "https://x/match" not in saved[-1]
    assert "Preparation failures: 1" in telegram.messages[-1]
    assert "fit: 1" in telegram.messages[-1]
    assert "No evaluated jobs matched" not in telegram.messages[-1]


def test_message_failure_remains_unseen_and_is_reported(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    _install_fit(monkeypatch, DeliveryOutcome(error=RuntimeError("message down")))

    run.run_daily(make_config())

    assert "https://x/match" not in saved[-1]
    assert "Fit notifications sent: 0" in telegram.messages[-1]
    assert "Delivery failures: 1" in telegram.messages[-1]


def test_document_failure_remains_unseen_and_reports_partial_delivery(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    _install_fit(
        monkeypatch,
        DeliveryOutcome(
            notification_sent=True,
            notification_satisfied=True,
            error=RuntimeError("document down"),
        ),
    )

    run.run_daily(make_config())

    assert "https://x/match" not in saved[-1]
    assert "Fit notifications sent: 1" in telegram.messages[-1]
    assert "Verified CVs delivered: 0" in telegram.messages[-1]
    assert "will retry" in telegram.messages[-1]


def test_complete_delivery_marks_seen_and_is_reported(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    _install_fit(
        monkeypatch,
        DeliveryOutcome(notification_sent=True, notification_satisfied=True, cv_sent=True),
    )

    run.run_daily(make_config())

    assert "https://x/match" in saved[-1]
    assert "match|acme" in saved[-1]
    assert "Fit notifications sent: 1" in telegram.messages[-1]
    assert "Verified CVs delivered: 1" in telegram.messages[-1]
    assert "Delivery failures: 0" in telegram.messages[-1]


def test_mixed_outcomes_have_accurate_summary(monkeypatch):
    jobs = [
        Job(title="No", company="A", url="https://x/no", description="x" * 200),
        Job(title="Good", company="B", url="https://x/good", description="x" * 200),
        Job(title="Partial", company="C", url="https://x/partial", description="x" * 200),
        Job(title="EvalFail", company="D", url="https://x/eval", description="x" * 200),
    ]
    telegram, saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)

    def evaluate(_client, _criteria, job):
        if job["title"] == "EvalFail":
            raise RuntimeError("eval down")
        return {"fit": job["title"] != "No", "reason": "result", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", evaluate)
    monkeypatch.setattr(
        run,
        "prepare_fit",
        lambda *_args: {"title": _args[3]["title"], "company": "x", "message": "fit", "pdf_bytes": b"PDF"},
    )
    outcomes = {
        "Good": DeliveryOutcome(notification_sent=True, notification_satisfied=True, cv_sent=True),
        "Partial": DeliveryOutcome(
            notification_sent=True,
            notification_satisfied=True,
            error=RuntimeError("upload"),
        ),
    }
    monkeypatch.setattr(
        run,
        "send_fit",
        lambda payload, _telegram, **_kwargs: outcomes[payload["title"]],
    )

    run.run_daily(make_config())

    summary = telegram.messages[-1]
    assert "New candidates: 4" in summary
    assert "Evaluated: 3 (non-fit: 1, fit: 2)" in summary
    assert "Fit notifications sent: 2" in summary
    assert "Verified CVs delivered: 1" in summary
    assert "Evaluation failures: 1" in summary
    assert "Delivery failures: 1" in summary
    assert "https://x/good" in saved[-1]
    assert "https://x/partial" not in saved[-1]
    assert "https://x/eval" not in saved[-1]


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


def test_first_preparation_failure_records_attempt_and_sends_pending_fit_once(monkeypatch):
    job = Job(title="<Match>", company="A&B", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 15))
    _install_fit(monkeypatch, prepare_error=RuntimeError("compile down"))

    run.run_daily(make_config())

    tokens = delivery_identity_tokens("https://x/match", "<Match>", "A&B", "")
    assert all(f"delivery:attempt:{token}:1:2026-07-16" in saved[-1] for token in tokens)
    assert all(f"delivery:notified:{token}" in saved[-1] for token in tokens)
    pending = [message for message in telegram.messages if "verified CV pending" in message]
    assert len(pending) == 1
    assert "great" in pending[0]
    assert "&lt;Match&gt;" in telegram.messages[-1]
    assert "Preparation, attempt 1, next retry 2026-07-16" in telegram.messages[-1]


def test_waiting_retry_skips_all_llm_work(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    seen = set()
    record_delivery_failure(
        seen,
        {"url": job.url, "title": job.title, "company": job.company, "location": job.location},
        datetime.date(2026, 7, 15),
        "preparation",
    )
    telegram, _saved = install_daily_fakes(monkeypatch, [job], initial_seen=seen)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 15))
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))
    monkeypatch.setattr(run, "prepare_fit", lambda *_args: (_ for _ in ()).throw(AssertionError("no preparation")))
    monkeypatch.setattr(run, "prepare_retry_fit", lambda *_args: (_ for _ in ()).throw(AssertionError("no retry")))

    run.run_daily(make_config())

    assert "Retries waiting for backoff: 1" in telegram.messages[-1]
    assert "Retry attempts executed: 0" in telegram.messages[-1]
    assert "New candidates: 0" in telegram.messages[-1]
    assert "No evaluated jobs matched" not in telegram.messages[-1]


def test_notified_due_retry_skips_evaluation_and_uploads_only_pdf(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    job_dict = {"url": job.url, "title": job.title, "company": job.company, "location": job.location}
    seen = set()
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 15), "document")
    mark_delivery_notified(seen, **job_dict)
    telegram, saved = install_daily_fakes(monkeypatch, [job], initial_seen=seen)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))
    monkeypatch.setattr(
        run,
        "prepare_retry_fit",
        lambda *_args: {"title": "Match", "company": "Acme", "pdf_bytes": b"PDF"},
    )

    run.run_daily(make_config())

    assert telegram.documents[0][1] == b"PDF"
    assert all("<b>Match</b>" not in message for message in telegram.messages)
    assert "https://x/match" in saved[-1]
    assert "Known-fit retries that skipped evaluation: 1" in telegram.messages[-1]


def test_failed_pending_notification_re_evaluates_on_due_retry(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram = FakeTelegram(fail_pending=True)
    _telegram, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    today = [datetime.date(2026, 7, 15)]
    monkeypatch.setattr(run, "_today", lambda: today[0])
    evaluations = []
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: evaluations.append(today[0]) or {"fit": True, "reason": "great", "timezone_note": None},
    )
    monkeypatch.setattr(run, "prepare_fit", lambda *_args: (_ for _ in ()).throw(RuntimeError("compile down")))

    run.run_daily(make_config())
    today[0] = datetime.date(2026, 7, 16)
    run.run_daily(make_config())

    assert evaluations == [datetime.date(2026, 7, 15), datetime.date(2026, 7, 16)]
    tokens = delivery_identity_tokens("https://x/match", "Match", "Acme", "")
    assert all(f"delivery:notified:{token}" not in saved[-1] for token in tokens)


def test_third_failure_blocks_without_seen_keys_and_sends_terminal_alert(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    job_dict = {"url": job.url, "title": job.title, "company": job.company, "location": job.location}
    seen = set()
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 15), "preparation")
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 16), "preparation")
    mark_delivery_notified(seen, **job_dict)
    telegram, saved = install_daily_fakes(monkeypatch, [job], initial_seen=seen)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 18))
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))
    monkeypatch.setattr(run, "prepare_retry_fit", lambda *_args: (_ for _ in ()).throw(RuntimeError("compile down")))

    run.run_daily(make_config())

    assert "https://x/match" not in saved[-1]
    assert "match|acme" not in saved[-1]
    assert any(marker.startswith("delivery:blocked:") for marker in saved[-1])
    assert len([message for message in telegram.messages if "automated CV delivery blocked" in message]) == 1
    assert any(marker.startswith("delivery:block-alerted:") for marker in saved[-1])
    assert "Newly blocked fits: 1" in telegram.messages[-1]


def test_blocked_job_only_retries_terminal_alert_until_acknowledged(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    job_dict = {"url": job.url, "title": job.title, "company": job.company, "location": job.location}
    seen = set()
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 15), "document")
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 16), "document")
    record_delivery_failure(seen, job_dict, datetime.date(2026, 7, 18), "document")
    telegram = FakeTelegram(fail_block=True)
    _telegram, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram, initial_seen=seen)
    monkeypatch.setattr(run, "evaluate_job", lambda *_args: (_ for _ in ()).throw(AssertionError("no evaluation")))
    monkeypatch.setattr(run, "prepare_fit", lambda *_args: (_ for _ in ()).throw(AssertionError("no preparation")))
    monkeypatch.setattr(run, "prepare_retry_fit", lambda *_args: (_ for _ in ()).throw(AssertionError("no retry")))

    run.run_daily(make_config())
    assert not any(marker.startswith("delivery:block-alerted:") for marker in saved[-1])
    telegram.fail_block = False
    run.run_daily(make_config())

    assert len([message for message in telegram.messages if "automated CV delivery blocked" in message]) == 1
    assert any(marker.startswith("delivery:block-alerted:") for marker in saved[-1])


def test_three_attempt_lifecycle_runs_on_days_zero_one_and_three(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    today = [datetime.date(2026, 7, 15)]
    evaluations = []
    preparations = []
    monkeypatch.setattr(run, "_today", lambda: today[0])
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: evaluations.append(today[0]) or {"fit": True, "reason": "great", "timezone_note": None},
    )
    monkeypatch.setattr(
        run,
        "prepare_fit",
        lambda *_args: preparations.append(today[0]) or (_ for _ in ()).throw(RuntimeError("compile down")),
    )
    monkeypatch.setattr(
        run,
        "prepare_retry_fit",
        lambda *_args: preparations.append(today[0]) or (_ for _ in ()).throw(RuntimeError("compile down")),
    )

    run.run_daily(make_config())
    assert any(":1:2026-07-16" in marker for marker in saved[-1])

    today[0] = datetime.date(2026, 7, 16)
    run.run_daily(make_config())
    assert any(":2:2026-07-18" in marker for marker in saved[-1])

    today[0] = datetime.date(2026, 7, 17)
    run.run_daily(make_config())
    assert "Retries waiting for backoff: 1" in telegram.messages[-1]

    today[0] = datetime.date(2026, 7, 18)
    run.run_daily(make_config())

    assert evaluations == [datetime.date(2026, 7, 15)]
    assert preparations == [
        datetime.date(2026, 7, 15),
        datetime.date(2026, 7, 16),
        datetime.date(2026, 7, 18),
    ]
    assert any(marker.startswith("delivery:blocked:") for marker in saved[-1])
    assert "will retry next run" not in telegram.messages[-1]


# ── audit order 4d: structured lifecycle seen-state (reopen wiring) ────────────

def test_reopen_on_description_change_reevaluates(monkeypatch):
    jobs = [Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)]
    _telegram, _saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    evaluations = []

    def counting_nonfit(_client, _criteria, job):
        evaluations.append(job["title"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", counting_nonfit)

    run.run_daily(make_config())
    # Same identity (url/title/company), different sufficient description.
    jobs[:] = [Job(title="Match", company="Acme", url="https://x/match", description="y" * 250)]
    run.run_daily(make_config())

    assert len(evaluations) == 2  # the content change reopens the prior non-fit


def test_same_description_is_not_reevaluated(monkeypatch):
    jobs = [Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)]
    _telegram, saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    evaluations = []

    def counting_nonfit(_client, _criteria, job):
        evaluations.append(job["title"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", counting_nonfit)

    run.run_daily(make_config())
    run.run_daily(make_config())

    assert len(evaluations) == 1  # unchanged content is skipped on the second run
    assert "https://x/match" in saved[-1]
    assert "match|acme" in saved[-1]


def test_delivered_fit_is_not_reopened_on_change(monkeypatch):
    jobs = [Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)]
    _telegram, saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    _install_fit(
        monkeypatch,
        DeliveryOutcome(notification_sent=True, notification_satisfied=True, cv_sent=True),
    )
    evaluations = []

    def counting_fit(_client, _criteria, job):
        evaluations.append(job["title"])
        return {"fit": True, "reason": "great", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", counting_fit)

    run.run_daily(make_config())
    # Change the description; a delivered fit must never be re-opened.
    jobs[:] = [Job(title="Match", company="Acme", url="https://x/match", description="z" * 300)]
    run.run_daily(make_config())

    assert len(evaluations) == 1
    assert any(
        marker.startswith("eval:verdict:") and marker.endswith(":fit")
        for marker in saved[-1]
    )


def test_legacy_seen_job_without_lifecycle_markers_stays_skipped(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    _telegram, _saved = install_daily_fakes(
        monkeypatch, [job], initial_seen={"https://x/match", "match|acme"},
    )
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: (_ for _ in ()).throw(AssertionError("legacy job must stay skipped")),
    )

    assert run.run_daily(make_config()) == 0  # skipped, evaluate never called


def test_criteria_change_reopens_prior_nonfit(monkeypatch):
    jobs = [Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)]
    _telegram, _saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    evaluations = []

    def counting_nonfit(_client, _criteria, job):
        evaluations.append(job["title"])
        return {"fit": False, "reason": "no", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", counting_nonfit)

    run.run_daily(make_config())  # criteria == "criteria" (harness default)
    monkeypatch.setattr(run, "load_criteria", lambda: "totally different criteria")
    run.run_daily(make_config())

    assert len(evaluations) == 2  # a criteria change reopens the prior non-fit


def test_reopened_job_that_defers_records_signature_and_stops_reopening(monkeypatch):
    # A job that reopens (criteria change) but whose description is now
    # insufficient must record a 'deferred' signature so it does not reopen and
    # re-defer every run. Without that record, ensure_job_description would run
    # every run forever.
    jobs = [Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)]
    _telegram, _saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 16))
    ensure_calls = []
    sufficiency = iter([True, False, False])

    def ensure(_job):
        ensure_calls.append(1)
        return next(sufficiency)

    monkeypatch.setattr(run, "ensure_job_description", ensure)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: {"fit": False, "reason": "no", "timezone_note": None},
    )

    run.run_daily(make_config())  # run 1: evaluated, non-fit
    monkeypatch.setattr(run, "load_criteria", lambda: "different criteria")
    run.run_daily(make_config())  # run 2: reopened, description now insufficient -> deferred
    run.run_daily(make_config())  # run 3: same content+criteria -> must NOT reopen

    assert len(ensure_calls) == 2  # run 3 was skipped thanks to the deferred signature


def test_uncertain_verdict_is_surfaced_for_review_and_marked_seen(monkeypatch):
    job = Job(title="Maybe", company="Acme", url="https://x/maybe", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_args: {
            "fit": False,
            "verdict": "uncertain",
            "reason": "policy could not decide",
            "timezone_note": None,
        },
    )
    monkeypatch.setattr(run, "prepare_fit", lambda *_a: (_ for _ in ()).throw(AssertionError("no tailoring")))

    run.run_daily(make_config())

    review = [m for m in telegram.messages if "flagged for review" in m]
    assert len(review) == 1
    assert "Maybe" in review[0]
    assert "policy could not decide" in review[0]
    # surfaced in the run summary, not counted as a non-fit
    assert "Needs review (uncertain): 1" in telegram.messages[-1]
    # marked seen (notified once) and recorded as an uncertain verdict
    assert "https://x/maybe" in saved[-1]
    assert any(m.startswith("eval:verdict:") and m.endswith(":uncertain") for m in saved[-1])

    # a second run does NOT re-surface it (marked seen)
    run.run_daily(make_config())
    assert len([m for m in telegram.messages if "flagged for review" in m]) == 1


# ── digest delivery (one ZIP per run: HTML dashboard + tailored CVs) ───────────

def _read_digest(telegram):
    """Return (filename, ZipFile, index.html text) for the last sent document."""
    import io
    import zipfile

    name, content, _caption = telegram.documents[-1]
    zf = zipfile.ZipFile(io.BytesIO(content))
    return name, zf, zf.read("index.html").decode("utf-8")


def _capture_digest_contexts(monkeypatch):
    """Record each DigestContext handed to build_digest_zip, still building the ZIP."""
    real = run.build_digest_zip
    contexts = []

    def spy(ctx):
        contexts.append(ctx)
        return real(ctx)

    monkeypatch.setattr(run, "build_digest_zip", spy)
    return contexts


def _install_digest_fit(monkeypatch, pdf=b"PDFDATA", summary="One-line summary."):
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {"fit": True, "reason": "great fit", "timezone_note": None, "facts": {}},
    )
    monkeypatch.setattr(
        run,
        "prepare_fit",
        lambda *_a: {"title": "Match", "company": "Acme", "message": "m", "pdf_bytes": pdf},
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: summary)


def test_digest_delivery_sends_one_zip_and_marks_fit_seen(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    # Exactly one message total — the ZIP document; no per-job or summary texts.
    assert telegram.messages == []
    assert len(telegram.documents) == 1
    name, zf, html = _read_digest(telegram)
    assert name == "job-digest-2026-07-21.zip"
    assert "Match" in html
    assert "great fit" in html
    assert "One-line summary." in html
    pdfs = [n for n in zf.namelist() if n.startswith("cvs/") and n.endswith(".pdf")]
    assert len(pdfs) == 1
    assert zf.read(pdfs[0]) == b"PDFDATA"
    assert 'href="{}"'.format(pdfs[0]) in html  # the local link resolves
    assert "https://x/match" in saved[-1]


def test_digest_delivery_failure_keeps_fit_unseen_and_schedules_retry(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram = FakeTelegram()

    def boom(*_a, **_k):
        raise RuntimeError("upload down")

    telegram.send_document = boom
    _t, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    assert "https://x/match" not in saved[-1]
    assert any(marker.startswith("delivery:attempt:") for marker in saved[-1])
    # The user must not be left in silence: a text fallback summary is sent.
    assert any("Job search complete" in m for m in telegram.messages)


def test_digest_folds_uncertain_and_deferred_into_zip_not_messages(monkeypatch):
    fit = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    maybe = Job(title="Maybe", company="Beta", url="https://x/maybe", description="x" * 200)
    sparse = Job(title="Sparse", company="Gamma", url="https://x/sparse", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [fit, maybe, sparse])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda job: job["title"] != "Sparse")

    def evaluate(_client, _criteria, job):
        if job["title"] == "Match":
            return {"fit": True, "reason": "great", "timezone_note": None, "facts": {}}
        return {"fit": False, "verdict": "uncertain", "reason": "cannot decide", "timezone_note": None}

    monkeypatch.setattr(run, "evaluate_job", evaluate)
    monkeypatch.setattr(
        run,
        "prepare_fit",
        lambda *_a: {"title": "Match", "company": "Acme", "message": "m", "pdf_bytes": b"PDF"},
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "sum")

    run.run_daily(make_config(digest_delivery=True))

    assert all("flagged for review" not in m for m in telegram.messages)
    assert all("posting deferred" not in m for m in telegram.messages)
    assert len(telegram.documents) == 1
    _name, _zf, html = _read_digest(telegram)
    assert "Maybe" in html and "cannot decide" in html
    assert "Sparse" in html


def test_digest_success_marks_uncertain_seen(monkeypatch):
    job = Job(title="Maybe", company="Acme", url="https://x/maybe", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {"fit": False, "verdict": "uncertain", "reason": "maybe", "timezone_note": None},
    )
    monkeypatch.setattr(run, "prepare_fit", lambda *_a: {"pdf_bytes": b"REVIEW-PDF"})
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    run.run_daily(make_config(digest_delivery=True))

    assert len(telegram.documents) == 1
    # A delivered digest marks the uncertain job seen so it isn't re-surfaced.
    assert "https://x/maybe" in saved[-1]
    assert any(m.startswith("eval:verdict:") and m.endswith(":uncertain") for m in saved[-1])


def test_digest_failure_keeps_uncertain_unseen_for_retry(monkeypatch):
    job = Job(title="Maybe", company="Acme", url="https://x/maybe", description="x" * 200)
    telegram = FakeTelegram()

    def boom(*_a, **_k):
        raise RuntimeError("upload down")

    telegram.send_document = boom
    _t, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {"fit": False, "verdict": "uncertain", "reason": "maybe", "timezone_note": None},
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    run.run_daily(make_config(digest_delivery=True))

    # A failed digest must NOT bury the uncertain job — it re-surfaces next run.
    assert "https://x/maybe" not in saved[-1]
    assert not any(m.startswith("eval:verdict:") and m.endswith(":uncertain") for m in saved[-1])


def test_digest_caption_counts_delivered_fits_not_found_fits(monkeypatch):
    good = Job(title="Good", company="Acme", url="https://x/good", description="x" * 200)
    bad = Job(title="Bad", company="Beta", url="https://x/bad", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [good, bad])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {"fit": True, "reason": "great", "timezone_note": None, "facts": {}},
    )

    def prepare(_llm, _instr, _base, job, _evaluation):
        if job["title"] == "Bad":
            raise RuntimeError("compile down")
        return {"title": "Good", "company": "Acme", "message": "m", "pdf_bytes": b"PDF"}

    monkeypatch.setattr(run, "prepare_fit", prepare)
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    run.run_daily(make_config(digest_delivery=True))

    # Two fits were found but only one compiled — the digest must say "1 fit".
    _name, _content, caption = telegram.documents[-1]
    assert "1 fit" in caption
    assert "2 fit" not in caption
    _n, _zf, html = _read_digest(telegram)
    assert "Good" in html
    assert "Bad" not in html  # the failed fit is not in the bundle


def test_digest_success_commits_deferral_markers(monkeypatch):
    sparse = Job(title="Sparse", company="Gamma", url="https://x/sparse", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [sparse])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: False)
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    run.run_daily(make_config(digest_delivery=True))

    assert len(telegram.documents) == 1
    assert any(marker.startswith("deferred:") for marker in saved[-1])


def test_digest_failure_does_not_commit_deferral_markers(monkeypatch):
    # finding N9: the deferral markers suppress repeat notices, and they were
    # committed in the candidate/eval loops — BEFORE the ZIP that announces them
    # was sent. A failed send therefore recorded the job as "already announced"
    # and the notice was never delivered. The review section already waited for a
    # successful send; deferrals now do too.
    sparse = Job(title="Sparse", company="Gamma", url="https://x/sparse", description="tiny")
    telegram = FakeTelegram()

    def boom(*_a, **_k):
        raise RuntimeError("upload down")

    telegram.send_document = boom
    _t, saved = install_daily_fakes(monkeypatch, [sparse], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: False)
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    run.run_daily(make_config(digest_delivery=True))

    assert not any(marker.startswith("deferred:") for marker in saved[-1])
    assert not any(m.startswith("eval:verdict:") and m.endswith(":deferred") for m in saved[-1])


def test_legacy_path_still_commits_deferral_markers_immediately(monkeypatch):
    # The legacy path sends its own deferred-jobs message, so it must keep
    # committing right away — otherwise repeat notices come back.
    sparse = Job(title="Sparse", company="Gamma", url="https://x/sparse", description="tiny")
    telegram, saved = install_daily_fakes(monkeypatch, [sparse])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: False)

    run.run_daily(make_config(digest_delivery=False))

    assert any("posting deferred" in m for m in telegram.messages)
    assert any(marker.startswith("deferred:") for marker in saved[-1])


def test_digest_does_not_recount_a_notification_for_a_retried_fit(monkeypatch):
    # finding N9: _deliver_digest counted a notification for EVERY prepared fit,
    # including ones announced in an earlier run. The legacy path avoids this by
    # threading notification_already_sent into send_fit.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    seen = set()
    mark_delivery_notified(seen, url="https://x/match", title="Match", company="Acme", location="")
    record_delivery_failure(
        seen,
        {"url": "https://x/match", "title": "Match", "company": "Acme", "location": ""},
        datetime.date(2026, 7, 20),
        "delivery",
    )
    telegram, _saved = install_daily_fakes(monkeypatch, [job], initial_seen=seen)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "prepare_retry_fit",
        lambda *_a: {"title": "Match", "company": "Acme", "message": "m", "pdf_bytes": b"PDF"},
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")

    contexts = _capture_digest_contexts(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    assert len(telegram.documents) == 1
    stats = contexts[-1].stats
    assert stats.notification_sent == 0  # already announced in an earlier run
    assert stats.cv_sent == 1            # ...but the CV did land today


def test_digest_failure_marks_the_fit_notified_so_the_retry_skips_evaluation(monkeypatch):
    # finding N9: a digest-failed fit was never marked notified, so the next run
    # re-ran fact extraction, bullet selection AND pdflatex from scratch. The
    # fallback run summary names each pending fit, so "notified" is accurate.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram = FakeTelegram()

    def boom(*_a, **_k):
        raise RuntimeError("upload down")

    telegram.send_document = boom
    _t, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    tokens = delivery_identity_tokens(
        url="https://x/match", title="Match", company="Acme", location="",
    )
    assert any(f"delivery:notified:{token}" in saved[-1] for token in tokens)
    # ...and the job itself is still unseen, so it does come back.
    assert "https://x/match" not in saved[-1]


def test_digest_reopened_defer_is_recorded_even_with_nothing_to_bundle(monkeypatch):
    # Review of PR #7: _register_deferral always queues a pending deferral, but
    # newly_deferred (and so deferred_entries) only gets jobs whose markers are
    # NEW. A job deferred in an earlier run, reopened today and still
    # description-poor, therefore produces an empty digest — and the zero-content
    # early return used to skip the commit loop entirely. The "deferred"
    # signature that stops the reopen->defer cycle never landed, so the job
    # re-paid ensure_job_description's URL fetch every single run, forever.
    jobs = [Job(title="Sparse", company="Gamma", url="https://x/sparse", description="x" * 200)]
    telegram, _saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    ensure_calls = []
    sufficiency = iter([True, False, False, False])

    def ensure(_job):
        ensure_calls.append(1)
        return next(sufficiency)

    monkeypatch.setattr(run, "ensure_job_description", ensure)
    monkeypatch.setattr(
        run, "evaluate_job", lambda *_a: {"fit": False, "reason": "no", "timezone_note": None}
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")
    cfg = make_config(digest_delivery=True)

    run.run_daily(cfg)  # run 1: evaluated, non-fit
    monkeypatch.setattr(run, "load_criteria", lambda: "criteria v2")
    run.run_daily(cfg)  # run 2: reopened, description-poor -> FIRST deferral, so it bundles
    monkeypatch.setattr(run, "load_criteria", lambda: "criteria v3")
    run.run_daily(cfg)  # run 3: reopened again, markers already seen -> nothing to bundle
    run.run_daily(cfg)  # run 4: unchanged since run 3 -> must NOT reopen

    assert len(telegram.documents) == 1   # only run 2 had anything new to announce
    assert len(ensure_calls) == 3         # run 4 skipped, thanks to run 3's deferred signature


def test_digest_zero_results_sends_text_summary_and_no_zip(monkeypatch):
    job = Job(title="No", company="Acme", url="https://x/no", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {"fit": False, "reason": "no", "timezone_note": None},
    )

    run.run_daily(make_config(digest_delivery=True))

    assert telegram.documents == []
    assert any("Job search complete" in m for m in telegram.messages)


def test_digest_sections_group_the_delivered_fits(monkeypatch, tmp_path):
    config_file = tmp_path / "sections.py"
    config_file.write_text(
        "from job_search.digest.sections import Section, on_job\n"
        "SECTIONS = [Section('Acme roles', match=on_job(lambda j: j.company == 'Acme'))]\n",
        encoding="utf-8",
    )
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    cfg = make_config(digest_delivery=True)
    cfg.sections_file = str(config_file)
    run.run_daily(cfg)

    _name, zf, html = _read_digest(telegram)
    assert "Acme roles" in html
    assert telegram.messages == []  # a valid config raises no alert
    # Grouping is presentation only: the same CV lands in the ZIP under the
    # same filename regardless of which sub-heading it renders under.
    pdfs = [n for n in zf.namelist() if n.startswith("cvs/") and n.endswith(".pdf")]
    assert len(pdfs) == 1
    assert zf.read(pdfs[0]) == b"PDFDATA"


def test_a_broken_sections_config_alerts_and_still_delivers_the_digest(monkeypatch, tmp_path):
    config_file = tmp_path / "sections.py"
    config_file.write_text("SECTIONS = 'not a list'\n", encoding="utf-8")
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    cfg = make_config(digest_delivery=True)
    cfg.sections_file = str(config_file)
    run.run_daily(cfg)

    # Alerted, and the digest still went out with the fit marked seen.
    assert any("Digest sections" in message for message in telegram.messages)
    assert len(telegram.documents) == 1
    _name, _zf, html = _read_digest(telegram)
    assert "Match" in html
    assert "https://x/match" in saved[-1]


def test_the_sections_alert_escapes_html_in_the_error_message(monkeypatch, tmp_path):
    # sections_error can embed a section name verbatim ({!r}); Telegram sends
    # with parse_mode=HTML, so an unescaped "<" either renders a stray tag or
    # makes Telegram reject the whole alert with a 400 that is never retried.
    config_file = tmp_path / "sections.py"
    config_file.write_text(
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('<b>Remote</b>'), Section('<b>Remote</b>')]\n",
        encoding="utf-8",
    )
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    cfg = make_config(digest_delivery=True)
    cfg.sections_file = str(config_file)
    run.run_daily(cfg)

    alert = next(m for m in telegram.messages if "Digest sections" in m)
    assert "&lt;b&gt;Remote&lt;/b&gt;" in alert
    assert "<b>Remote</b>" not in alert


def test_a_render_time_predicate_failure_sends_no_telegram_message(monkeypatch, tmp_path):
    # The load-time smoke check runs the predicate against blank entries only;
    # it must not raise there (else it becomes a sections_error / alert case,
    # covered above). This predicate only explodes on a real entry's data, so
    # the failure surfaces solely as a render-time warning collected inside
    # render_digest_html — nothing should reach Telegram for it.
    config_file = tmp_path / "sections.py"
    config_file.write_text(
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section("
        "'Explode', match=lambda e: 1 / 0 if e.job.company == 'Acme' else True"
        ")]\n",
        encoding="utf-8",
    )
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    cfg = make_config(digest_delivery=True)
    cfg.sections_file = str(config_file)
    run.run_daily(cfg)

    assert telegram.messages == []
    assert len(telegram.documents) == 1
    _name, _zf, html = _read_digest(telegram)
    assert "Match" in html  # the digest was still delivered
    assert "ZeroDivisionError" in html  # the warning is visible in the dashboard


# ── telegraph delivery (one message: a page link + the CV password) ───────────

class FakeTelegraphClient:
    def __init__(self, fail_create=False):
        self.fail_create = fail_create
        self.created = []
        self.edited = []
        self.pages = []

    def get_page_list(self, _token, offset=0):
        return list(self.pages)

    def create_page(self, _token, title, nodes):
        if self.fail_create:
            raise RuntimeError("telegraph down")
        self.created.append((title, nodes))
        page = {"path": title.replace(" ", "-"), "title": title,
                "url": "https://telegra.ph/" + title.replace(" ", "-")}
        self.pages.insert(0, page)
        return page

    def edit_page(self, _token, path, title, nodes):
        self.edited.append((path, title, nodes))
        return {"path": path, "title": title, "url": "https://telegra.ph/" + path}


def _install_telegraph(monkeypatch, client=None):
    client = client or FakeTelegraphClient()
    monkeypatch.setattr(run, "TelegraphClient", lambda: client)
    return client


class FakeX0Client:
    """Records every upload; ``fail_upload`` makes the Nth one raise."""

    def __init__(self, fail_upload=None):
        self.fail_upload = fail_upload   # None, or a 1-based call number
        self.uploads = []                # (filename, content)

    def upload(self, filename, content):
        self.uploads.append((filename, content))
        if self.fail_upload == len(self.uploads):
            from job_search.notify.x0 import FileHostError

            raise FileHostError("host down")
        return "https://x0.at/{}_{}".format(filename.rsplit(".", 1)[0], "i" * 24)


def _install_x0(monkeypatch, client=None, archive_builder=None):
    """Install the file-host fake and a deterministic archive password."""
    client = client or FakeX0Client()
    monkeypatch.setattr(run, "X0Client", lambda: client)
    if archive_builder is not None:
        monkeypatch.setattr(run, "build_encrypted_cv_zip", archive_builder)
    monkeypatch.setattr(run, "new_password", lambda: "test-password-1234")
    return client


def _created_nodes(client):
    return repr(client.created[-1][1])


def _telegraph_config():
    return make_config(digest_delivery=True, telegraph_access_token="tok")


def test_telegraph_sends_one_message_with_one_hosted_cv_archive(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    # ONE message, no documents: the page links one protected archive.
    assert len(telegram.messages) == 1
    assert "https://telegra.ph/Job-Digest-2026-07-21-" in telegram.messages[0]
    assert telegram.documents == []
    # The host receives only the combined archive, never an individual PDF.
    names = [name for name, _content in host.uploads]
    assert names == ["job-cvs-2026-07-21.zip"]
    nodes = _created_nodes(client)
    assert "https://x0.at/igor_pivnyk_cv_acme_" not in nodes
    assert "https://x0.at/job-cvs-2026-07-21_" in nodes
    # The job is marked seen exactly as the ZIP path marks it.
    assert "https://x/match" in saved[-1]
    assert client.edited


def test_host_receives_only_an_encrypted_archive_of_plain_pdfs(monkeypatch):
    """The host gets ciphertext; extraction yields a normal PDF."""
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    _t, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch, pdf=b"PLAINTEXTCV")
    _install_telegraph(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert len(host.uploads) == 1
    zip_name, zip_content = host.uploads[0]
    assert zip_name == "job-cvs-2026-07-21.zip"
    assert b"PLAINTEXTCV" not in zip_content
    with pyzipper.AESZipFile(io.BytesIO(zip_content)) as archive:
        with pytest.raises(RuntimeError, match="Bad password"):
            archive.read("igor_pivnyk_cv_acme.pdf", pwd=b"wrong")
        assert archive.read(
            "igor_pivnyk_cv_acme.pdf", pwd=b"test-password-1234"
        ) == b"PLAINTEXTCV"


def test_the_password_is_in_the_message_and_not_on_the_page(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    message = telegram.messages[0]
    assert "test-password-1234" in message
    assert "<code>test-password-1234</code>" in message  # tap-to-copy in Telegram
    # Second line, above the counts: bound_message truncates from the end, and a
    # truncated password is a silently unusable digest.
    assert message.splitlines()[1].strip().endswith("</code>")
    assert "test-password-1234" not in _created_nodes(client)


def test_cvs_are_uploaded_before_the_page_is_published(monkeypatch):
    # The page needs the URLs, and an upload failure must leave nothing to
    # retract — so the order is not an implementation detail.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    _t, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    order = []
    client = FakeTelegraphClient()
    real_create = client.create_page
    client.create_page = lambda *a, **kw: (order.append("publish"), real_create(*a, **kw))[1]
    _install_telegraph(monkeypatch, client)
    host = FakeX0Client()
    real_upload = host.upload
    host.upload = lambda *a, **kw: (order.append("upload"), real_upload(*a, **kw))[1]
    _install_x0(monkeypatch, host)

    run.run_daily(_telegraph_config())

    assert order.index("upload") < order.index("publish")
    assert order.count("upload") == 1


def test_review_job_is_tailored_and_added_to_the_hosted_archive(monkeypatch):
    job = Job(title="Maybe", company="Beta", url="https://x/maybe", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run,
        "evaluate_job",
        lambda *_a: {
            "fit": False,
            "verdict": "uncertain",
            "reason": "unclear",
            "timezone_note": None,
            "facts": {},
        },
    )
    tailored = []

    def prepare(_llm, _instructions, _base, candidate, evaluation):
        tailored.append((candidate.title, evaluation["verdict"]))
        return {"pdf_bytes": b"REVIEW-PLAINTEXT"}

    monkeypatch.setattr(run, "prepare_fit", prepare)
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "summary")
    _install_telegraph(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert tailored == [("Maybe", "uncertain")]
    assert len(host.uploads) == 1
    with pyzipper.AESZipFile(io.BytesIO(host.uploads[0][1])) as archive:
        names = archive.namelist()
        assert names == ["igor_pivnyk_cv_beta.pdf"]
        assert archive.read(names[0], pwd=b"test-password-1234") == b"REVIEW-PLAINTEXT"
    assert "https://x/maybe" in saved[-1]
    assert len(telegram.messages) == 1


def test_archive_encryption_unavailable_falls_back_and_uploads_nothing(monkeypatch):
    """An unavailable AES writer falls back without exposing plaintext."""

    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)

    def no_encryption(_entries, _password):
        raise ImportError("pyzipper is not installed")

    host = _install_x0(monkeypatch, archive_builder=no_encryption)

    run.run_daily(_telegraph_config())

    # Nothing was uploaded and no page was created — nothing to retract.
    assert host.uploads == []
    assert client.created == []
    assert telegram.messages == []
    name, _zf, html = _read_digest(telegram)
    assert name == "job-digest-2026-07-21.zip"
    assert "Match" in html
    # ZIP semantics: the fit is delivered and committed, not retried.
    assert "https://x/match" in saved[-1]
    assert not any(marker.startswith("delivery:attempt:") for marker in saved[-1])


def test_an_upload_failure_falls_back_to_the_zip_with_no_page(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch, FakeX0Client(fail_upload=1))

    run.run_daily(_telegraph_config())

    assert client.created == []
    assert telegram.messages == []
    name, _zf, _html = _read_digest(telegram)
    assert name == "job-digest-2026-07-21.zip"
    assert "https://x/match" in saved[-1]


def test_a_failed_archive_upload_leaves_no_half_linked_page(monkeypatch):
    # If the sole archive upload fails, no page is published and Telegram gets
    # the self-contained fallback ZIP.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch, FakeX0Client(fail_upload=1))

    run.run_daily(_telegraph_config())

    assert client.created == []
    assert telegram.documents[-1][0] == "job-digest-2026-07-21.zip"


def test_a_run_with_no_fits_still_publishes_a_page(monkeypatch):
    # Nothing to upload is not a failure: a review-only run must still get its
    # page rather than silently dropping to the ZIP.
    job = Job(title="Maybe", company="Acme", url="https://x/maybe", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run, "evaluate_job",
        lambda *_a: {"fit": False, "verdict": "uncertain", "reason": "unclear",
                     "timezone_note": None, "facts": {}},
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "s")
    client = _install_telegraph(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert host.uploads == []
    assert client.created
    assert len(telegram.messages) == 1
    assert "telegra.ph" in telegram.messages[0]
    # No CVs, so no password and no download block.
    assert "test-password-1234" not in telegram.messages[0]
    assert "Download all CVs" not in _created_nodes(client)


def test_without_a_token_the_file_host_is_never_touched(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    assert host.uploads == []
    assert telegram.documents[-1][0] == "job-digest-2026-07-21.zip"


def test_a_telegraph_failure_after_successful_uploads_still_sends_the_zip(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    _install_telegraph(monkeypatch, FakeTelegraphClient(fail_create=True))
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert len(host.uploads) == 1      # the archive upload expires unused
    assert telegram.messages == []
    name, _zf, html = _read_digest(telegram)
    assert name == "job-digest-2026-07-21.zip"
    # The archive keeps its local links, so the ZIP is self-contained as ever.
    assert 'cvs/igor_pivnyk_cv_acme.pdf' in html
    assert "https://x/match" in saved[-1]


def test_every_fit_commits_when_the_page_is_delivered(monkeypatch):
    # The per-CV document loop is gone, and with it the only way a fit could
    # fail after the page went out. A delivered page means every CV was
    # delivered, so all three commit.
    jobs = [
        Job(title="Match {}".format(i), company="Acme{}".format(i),
            url="https://x/match{}".format(i), description="x" * 200)
        for i in (1, 2, 3)
    ]
    telegram, saved = install_daily_fakes(monkeypatch, jobs)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    monkeypatch.setattr(run, "ensure_job_description", lambda _job: True)
    monkeypatch.setattr(
        run, "evaluate_job",
        lambda *_a: {"fit": True, "reason": "great fit", "timezone_note": None, "facts": {}},
    )
    monkeypatch.setattr(
        run, "prepare_fit",
        lambda _llm, _ti, _bt, job, _ev: {
            "title": job["title"], "company": job["company"],
            "message": "m", "pdf_bytes": b"PDFDATA",
        },
    )
    monkeypatch.setattr(run, "summarize_job", lambda _llm, _job: "summary")
    _install_telegraph(monkeypatch)
    host = _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert len(telegram.messages) == 1
    assert telegram.documents == []
    assert len(host.uploads) == 1      # one archive containing all three CVs
    for index in (1, 2, 3):
        assert "https://x/match{}".format(index) in saved[-1]
    assert not any(marker.startswith("delivery:attempt:") for marker in saved[-1])


def test_telegraph_failure_falls_back_to_the_zip(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    _install_telegraph(monkeypatch, FakeTelegraphClient(fail_create=True))
    _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert telegram.messages == []
    name, _zf, html = _read_digest(telegram)
    assert name == "job-digest-2026-07-21.zip"
    assert "Match" in html
    assert "https://x/match" in saved[-1]


def test_no_token_still_sends_the_zip(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    run.run_daily(make_config(digest_delivery=True))

    assert telegram.documents[-1][0] == "job-digest-2026-07-21.zip"


def test_link_message_failure_keeps_the_fit_unseen_and_retries(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram = FakeTelegram()
    sent = []

    def send_message(message):
        if "telegra.ph" in message:
            raise RuntimeError("telegram down")
        sent.append(message)

    telegram.send_message = send_message
    _t, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    _install_telegraph(monkeypatch)
    _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    assert "https://x/match" not in saved[-1]
    assert any(marker.startswith("delivery:attempt:") for marker in saved[-1])
    assert any("Job search complete" in m for m in sent)   # the text fallback still lands


def test_index_refresh_failure_does_not_fail_the_run(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("edit down")

    client.edit_page = boom

    assert run.run_daily(_telegraph_config()) == 0
    assert "https://x/match" in saved[-1]


def test_zip_build_failure_is_fatal_not_a_delivery_failure(monkeypatch):
    # build_digest_zip raising is a bug (a render error, a bad entry) with
    # nothing to do with delivery — it must escape _deliver_digest and hit
    # run_daily's fatal path (error notification, re-raise) exactly as it did
    # before telegra.ph delivery existed, not consume a retry attempt.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)

    def boom(_ctx):
        raise RuntimeError("render bug")

    monkeypatch.setattr(run, "build_digest_zip", boom)

    with pytest.raises(RuntimeError, match="render bug"):
        run.run_daily(make_config(digest_delivery=True))

    assert not any(marker.startswith("delivery:attempt:") for marker in saved[-1])
    assert not any(marker.startswith("delivery:notified:") for marker in saved[-1])


def test_failed_link_message_retracts_the_orphaned_page(monkeypatch):
    # The page is created before the message announcing it can be sent, so a
    # failed send leaves a page nobody was told about — and the fits retry, so
    # the next run publishes another. The orphan must be withdrawn.
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram = FakeTelegram()

    def send_message(message):
        if "telegra.ph" in message:
            raise RuntimeError("telegram down")

    telegram.send_message = send_message
    _t, saved = install_daily_fakes(monkeypatch, [job], telegram=telegram)
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    from job_search.digest.telegraph import RETRACTED_TITLE

    retitled = [(path, title) for path, title, _n in client.edited if title == RETRACTED_TITLE]
    assert retitled, "the orphaned page was left live in the index"
    assert retitled[0][0].startswith("Job-Digest-2026-07-21-")
    # The fit still retries exactly as before — retraction changes no state.
    assert "https://x/match" not in saved[-1]
    assert any(marker.startswith("delivery:attempt:") for marker in saved[-1])


def test_successful_delivery_retracts_nothing(monkeypatch):
    job = Job(title="Match", company="Acme", url="https://x/match", description="x" * 200)
    telegram, _saved = install_daily_fakes(monkeypatch, [job])
    monkeypatch.setattr(run, "_today", lambda: datetime.date(2026, 7, 21))
    _install_digest_fit(monkeypatch)
    client = _install_telegraph(monkeypatch)
    _install_x0(monkeypatch)

    run.run_daily(_telegraph_config())

    from job_search.digest.telegraph import RETRACTED_TITLE

    assert not any(title == RETRACTED_TITLE for _p, title, _n in client.edited)
