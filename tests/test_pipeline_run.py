"""Regressions for description gating in the scheduled pipeline."""
import datetime
from types import SimpleNamespace

from job_search.models import Job
from job_search.pipeline import run
from job_search.pipeline import stages
from job_search.pipeline.stages import DeliveryOutcome
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


def make_config():
    return SimpleNamespace(
        gemini_api_key="gemini",
        qwen_api_key="qwen",
        telegram_bot_token="token",
        telegram_chat_id="chat",
        eval_workers=2,
        tailor_workers=1,
        sources_enable=(),
        sources_disable=(),
        state_sync=False,
    )


def install_daily_fakes(monkeypatch, jobs, telegram=None, initial_seen=None):
    telegram = telegram or FakeTelegram()
    state = set(initial_seen or ())
    saved = []

    def save(seen):
        state.clear()
        state.update(seen)
        saved.append(set(state))

    monkeypatch.setattr(run, "TelegramClient", lambda *_args: telegram)
    monkeypatch.setattr(run, "LLMClient", lambda *_args: FakeLLM())
    monkeypatch.setattr(run, "load_criteria", lambda: "criteria")
    monkeypatch.setattr(run, "load_tailoring_instructions", lambda: "instructions")
    monkeypatch.setattr(run, "load_base_tex", lambda: "base")
    monkeypatch.setattr(run, "fetch_jobs", lambda **_kwargs: list(jobs))
    monkeypatch.setattr(run, "load_seen_jobs", lambda: set(state))
    monkeypatch.setattr(run, "save_seen_jobs", save)
    return telegram, saved


def test_deferred_markers_ignore_semantically_empty_job_identity():
    tc_key = run.title_company_key("", "", "")

    assert tc_key == "|"
    assert run._deferred_markers("", tc_key) == set()


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
