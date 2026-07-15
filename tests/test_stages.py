"""Characterization tests for the pipeline stages with injected Telegram.

These guard the Telegram dependency-injection refactor: stages take an explicit
TelegramClient-shaped object instead of reading module globals.
"""
import pytest

# --- modules under test (repoint on migration) ---
from job_search.pipeline import stages
from job_search.pipeline.stages import (
    CVDeliveryError,
    CVPreparationError,
    _send_error_notification,
    prepare_fit,
    prepare_retry_fit,
    process_job,
    send_fit,
    tailor_single_job,
)


CLEAN_CV = (
    "\\documentclass[9.5pt]{article}\\begin{document}"
    "\\jobheader{Check Point}\\jobheader{Applitools}"
    "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}"
)


class FakeTelegram:
    def __init__(self, raise_on_message=False, raise_on_document=False):
        self.messages = []
        self.documents = []
        self._raise_on_message = raise_on_message
        self._raise_on_document = raise_on_document

    def send_message(self, text):
        if self._raise_on_message:
            raise RuntimeError("telegram down")
        self.messages.append(text)

    def send_document(self, filename, content, caption):
        if self._raise_on_document:
            raise RuntimeError("document upload down")
        self.documents.append((filename, content, caption))


def _payload(pdf_bytes=b"PDF"):
    return {"title": "iOS", "company": "Acme", "message": "hi", "pdf_bytes": pdf_bytes}


def test_send_fit_complete_delivery():
    tg = FakeTelegram()
    outcome = send_fit(_payload(), tg)

    assert outcome.complete is True
    assert outcome.notification_sent is True
    assert outcome.notification_satisfied is True
    assert outcome.cv_sent is True
    assert outcome.error is None
    assert tg.messages == ["hi"]
    assert len(tg.documents) == 1
    name, content, _caption = tg.documents[0]
    assert name == "igor_pivnyk_cv_acme.pdf"
    assert content == b"PDF"


def test_send_fit_rejects_missing_pdf_before_telegram():
    tg = FakeTelegram()
    outcome = send_fit(_payload(None), tg)

    assert outcome.complete is False
    assert outcome.notification_sent is False
    assert outcome.cv_sent is False
    assert outcome.error is not None
    assert tg.messages == []
    assert tg.documents == []


def test_send_fit_reports_message_failure():
    tg = FakeTelegram(raise_on_message=True)
    outcome = send_fit(_payload(), tg)

    assert outcome.complete is False
    assert outcome.notification_sent is False
    assert outcome.cv_sent is False
    assert isinstance(outcome.error, RuntimeError)
    assert tg.documents == []


def test_send_fit_reports_document_failure_after_message():
    tg = FakeTelegram(raise_on_document=True)
    outcome = send_fit(_payload(), tg)

    assert outcome.complete is False
    assert outcome.notification_sent is True
    assert outcome.notification_satisfied is True
    assert outcome.cv_sent is False
    assert isinstance(outcome.error, RuntimeError)
    assert tg.messages == ["hi"]


def test_send_fit_skips_repeat_notification_for_pdf_only_retry():
    tg = FakeTelegram()

    outcome = send_fit(_payload(), tg, notification_already_sent=True)

    assert outcome.complete is True
    assert outcome.notification_sent is False
    assert outcome.notification_satisfied is True
    assert outcome.cv_sent is True
    assert tg.messages == []
    assert len(tg.documents) == 1


def test_send_fit_pdf_only_failure_keeps_notification_satisfied():
    tg = FakeTelegram(raise_on_document=True)

    outcome = send_fit(_payload(), tg, notification_already_sent=True)

    assert outcome.complete is False
    assert outcome.notification_sent is False
    assert outcome.notification_satisfied is True
    assert outcome.cv_sent is False
    assert tg.messages == []


def test_prepare_fit_requires_verified_pdf(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (False, None, tex))
    client = fake_llm([CLEAN_CV])

    with pytest.raises(CVPreparationError):
        prepare_fit(client, "instr", "base", {"title": "iOS", "company": "Acme"}, {"fit": True})


def test_prepare_fit_payload_contains_only_delivery_fields(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (True, b"PDF", tex))
    client = fake_llm([CLEAN_CV])

    payload = prepare_fit(
        client,
        "instr",
        "base",
        {"title": "iOS", "company": "Acme"},
        {"fit": True, "reason": "great"},
    )

    assert set(payload) == {"title", "company", "message", "pdf_bytes"}
    assert payload["pdf_bytes"] == b"PDF"


def test_prepare_retry_fit_builds_verified_pdf_without_evaluation_message(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (True, b"PDF", tex))
    client = fake_llm([CLEAN_CV])

    payload = prepare_retry_fit(
        client,
        "instr",
        "base",
        {"title": "iOS", "company": "Acme"},
    )

    assert payload == {"title": "iOS", "company": "Acme", "pdf_bytes": b"PDF"}


def test_prepare_fit_revalidates_compiler_repair_output(monkeypatch):
    repaired_with_false_claim = CLEAN_CV.replace(
        "\\end{document}",
        "Built consumer banking systems.\\end{document}",
    )
    monkeypatch.setattr(stages, "tailor_resume", lambda *_args: CLEAN_CV)
    monkeypatch.setattr(
        stages,
        "compile_with_fixes",
        lambda _client, _tex: (True, b"PDF", repaired_with_false_claim),
    )

    with pytest.raises(CVPreparationError) as raised:
        prepare_fit(
            object(),
            "instr",
            "base",
            {"title": "iOS", "company": "Acme"},
            {"fit": True, "reason": "great"},
        )

    assert "banking" in str(raised.value)


def test_process_job_not_fit(fake_llm):
    gemini = fake_llm(['{"fit": false, "reason": "no apple work", "timezone_note": null}'])
    tg = FakeTelegram()
    result = process_job(gemini, "crit", "instr", "base", {"title": "iOS", "company": "Acme"}, tg)
    assert result is False
    assert tg.messages == []
    assert tg.documents == []


def test_process_job_fit_sends(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (True, b"PDF", tex))
    gemini = fake_llm(['{"fit": true, "reason": "great", "timezone_note": null}', CLEAN_CV])
    tg = FakeTelegram()
    result = process_job(gemini, "crit", "instr", "base", {"title": "iOS", "company": "Acme"}, tg)
    assert result is True
    assert len(tg.messages) == 1
    assert tg.documents[0][0] == "igor_pivnyk_cv_acme.pdf"


def test_process_job_raises_for_incomplete_delivery(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (True, b"PDF", tex))
    gemini = fake_llm(['{"fit": true, "reason": "great", "timezone_note": null}', CLEAN_CV])
    tg = FakeTelegram(raise_on_document=True)

    with pytest.raises(CVDeliveryError) as raised:
        process_job(gemini, "crit", "instr", "base", {"title": "iOS", "company": "Acme"}, tg)

    assert raised.value.outcome.notification_sent is True
    assert raised.value.outcome.cv_sent is False


def test_tailor_single_job(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (True, b"PDF", tex))
    monkeypatch.setattr(stages, "load_tailoring_instructions", lambda: "instr")
    monkeypatch.setattr(stages, "load_base_tex", lambda: "base")
    gemini = fake_llm([CLEAN_CV])
    tg = FakeTelegram()
    tailor_single_job(gemini, {"title": "iOS Dev", "company": "Acme", "url": "u", "location": "Berlin"}, tg)
    assert len(tg.messages) == 1
    assert "iOS Dev" in tg.messages[0]
    assert tg.documents[0][0] == "igor_pivnyk_cv_acme.pdf"


def test_tailor_single_job_refuses_invalid_artifact(monkeypatch, fake_llm):
    monkeypatch.setattr(stages, "compile_with_fixes", lambda client, tex: (False, None, tex))
    monkeypatch.setattr(stages, "load_tailoring_instructions", lambda: "instr")
    monkeypatch.setattr(stages, "load_base_tex", lambda: "base")
    tg = FakeTelegram()

    with pytest.raises(CVPreparationError):
        tailor_single_job(
            fake_llm([CLEAN_CV]),
            {"title": "iOS Dev", "company": "Acme"},
            tg,
        )

    assert tg.messages == []
    assert tg.documents == []


def test_send_error_notification_swallows_failure():
    tg = FakeTelegram(raise_on_message=True)
    # must not raise even though send_message blows up
    _send_error_notification(RuntimeError("boom"), tg)

    tg2 = FakeTelegram()
    _send_error_notification(RuntimeError("boom"), tg2)
    assert "Pipeline error" in tg2.messages[0]
