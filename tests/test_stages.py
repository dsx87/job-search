"""Characterization tests for the pipeline stages with injected Telegram.

These guard the Telegram dependency-injection refactor: stages take an explicit
TelegramClient-shaped object instead of reading module globals.
"""
# --- modules under test (repoint on migration) ---
from job_search.pipeline import stages
from job_search.pipeline.stages import send_fit, process_job, tailor_single_job, _send_error_notification


CLEAN_CV = (
    "\\documentclass[9.5pt]{article}\\begin{document}"
    "\\jobheader{Check Point}\\jobheader{Applitools}"
    "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}"
)


class FakeTelegram:
    def __init__(self, raise_on_message=False):
        self.messages = []
        self.documents = []
        self._raise = raise_on_message

    def send_message(self, text):
        if self._raise:
            raise RuntimeError("telegram down")
        self.messages.append(text)

    def send_document(self, filename, content, caption):
        self.documents.append((filename, content, caption))


def test_send_fit_with_pdf():
    tg = FakeTelegram()
    send_fit({"title": "iOS", "company": "Acme", "message": "hi", "pdf_bytes": b"PDF", "final_tex": "tex"}, tg)
    assert tg.messages == ["hi"]
    assert len(tg.documents) == 1
    name, content, _caption = tg.documents[0]
    assert name == "igor_pivnyk_cv_acme.pdf"
    assert content == b"PDF"


def test_send_fit_tex_fallback():
    tg = FakeTelegram()
    send_fit({"title": "iOS", "company": "Acme", "message": "hi", "pdf_bytes": None, "final_tex": "TEXSRC"}, tg)
    assert len(tg.documents) == 1
    name, content, _caption = tg.documents[0]
    assert name == "igor_pivnyk_cv_acme.tex"
    assert content == b"TEXSRC"


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


def test_send_error_notification_swallows_failure():
    tg = FakeTelegram(raise_on_message=True)
    # must not raise even though send_message blows up
    _send_error_notification(RuntimeError("boom"), tg)

    tg2 = FakeTelegram()
    _send_error_notification(RuntimeError("boom"), tg2)
    assert "Pipeline error" in tg2.messages[0]
