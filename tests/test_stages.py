"""Characterization tests for the pipeline stages with injected Telegram.

These guard the Telegram dependency-injection refactor: stages take an explicit
TelegramClient-shaped object instead of reading module globals.
"""
import pytest

# --- modules under test (repoint on migration) ---
from job_search.components import CVArtifact
from job_search.models import Job
from job_search.pipeline import stages
from job_search.pipeline.stages import (
    CVDeliveryError,
    CVPreparationError,
    send_fit,
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
    artifact = (
        CVArtifact("igor_pivnyk_cv_acme.pdf", "application/pdf", pdf_bytes)
        if pdf_bytes
        else None
    )
    return {"title": "iOS", "company": "Acme", "message": "hi", "artifact": artifact}


def test_description_enrichment_updates_legacy_mapping():
    legacy = {"url": "https://x/job", "description": "tiny"}

    assert stages.ensure_job_description(
        legacy, fetcher=lambda _url: "complete text", min_length=10
    ) is True
    assert legacy["description"] == "complete text"


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


def test_format_notification_escapes_html_in_fields():
    from job_search.pipeline.stages import _format_notification

    job = Job(
        title="R&D iOS Engineer",
        company="AT&T",
        location="Denver <HQ>",
        url="https://x/1",
        source="arc",
        description="d",
    )
    msg = _format_notification(job, {"reason": "great <fit>", "timezone_note": "US <hours>"})
    assert "R&amp;D iOS Engineer" in msg
    assert "AT&amp;T" in msg
    assert "Denver &lt;HQ&gt;" in msg
    assert "great &lt;fit&gt;" in msg
    assert "US &lt;hours&gt;" in msg
    assert 'href="https://x/1"' in msg


def test_format_notification_omits_posting_link_without_url():
    from job_search.pipeline.stages import _format_notification

    msg = _format_notification(
        Job(title="iOS Engineer", company="Acme", description="d"),
        {"reason": "Manual tailoring"},
    )

    assert "View posting" not in msg
    assert 'href=""' not in msg


def test_format_uncertain_notification_escapes_and_includes_reason():
    from job_search.pipeline.stages import _format_uncertain_notification

    items = [
        (
            Job(title="R&D iOS", company="A<B>", url="https://x/1", description="d"),
            {"reason": "unclear <arrangement>"},
        ),
    ]
    msg = _format_uncertain_notification(items)
    assert "flagged for review" in msg
    assert "R&amp;D iOS" in msg
    assert "A&lt;B&gt;" in msg
    assert "unclear &lt;arrangement&gt;" in msg
    assert 'href="https://x/1"' in msg
