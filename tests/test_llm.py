"""Characterization tests for the LLM circuit-breaker and LLM tasks."""
import urllib.error

import pytest

# --- modules under test (repoint on migration) ---
from job_search.llm.clients import LLMClient
from job_search.llm.eval import evaluate_job
from job_search.llm.tailor import tailor_resume


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class _FakeModel:
    """Stand-in for GeminiClient/QwenClient. Returns canned values or raises
    queued exceptions, in order."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def generate(self, prompt, temperature=0.0, json_mode=False):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(gemini_items, qwen_items):
    c = LLMClient("g", "q")
    c.gemini = _FakeModel(gemini_items)
    c.qwen = _FakeModel(qwen_items)
    return c


def test_circuit_breaker_disables_gemini_on_429():
    c = _client([_http_error(429)], ["Q1", "Q2"])
    assert c.generate("p1") == "Q1"
    assert c._gemini_disabled is True
    # subsequent requests skip Gemini entirely
    assert c.generate("p2") == "Q2"
    assert c.gemini.calls == 1
    assert c._gemini_calls == 0
    assert c._qwen_calls == 2
    assert "disabled mid-run" in c.usage_summary()


def test_circuit_breaker_503_also_trips():
    c = _client([_http_error(503)], ["Q"])
    assert c.generate("p") == "Q"
    assert c._gemini_disabled is True


def test_non_circuit_http_error_falls_back_without_disabling():
    c = _client([_http_error(500), "G2"], ["Q"])
    assert c.generate("p1") == "Q"      # per-request fallback
    assert c._gemini_disabled is False  # still enabled
    assert c.generate("p2") == "G2"     # next request hits Gemini again
    assert c._gemini_calls == 1
    assert c._qwen_calls == 1


def test_success_path_counts_gemini():
    c = _client(["G"], [])
    assert c.generate("p") == "G"
    assert c._gemini_calls == 1
    assert c._qwen_calls == 0


def test_no_qwen_reraises_on_disable():
    c = LLMClient("g")  # no qwen key
    c.gemini = _FakeModel([_http_error(429)])
    with pytest.raises(RuntimeError):
        c.generate("p")


def test_evaluate_job_parses_and_coerces_fit(fake_llm):
    client = fake_llm(['{"fit": "true", "reason": "good match", "timezone_note": null}'])
    result = evaluate_job(client, "MY CRITERIA", {"title": "iOS Engineer", "company": "Acme"})
    assert result["fit"] is True
    assert result["reason"] == "good match"
    assert result["timezone_note"] is None
    # the prompt carried the criteria and job fields
    assert "MY CRITERIA" in client.prompts[0]
    assert "iOS Engineer" in client.prompts[0]


def test_tailor_resume_returns_clean_first_pass(fake_llm):
    cv = ("\\documentclass[9.5pt]{article}\\begin{document}"
          "\\jobheader{Check Point}\\jobheader{Applitools}"
          "\\jobheader{Shutterfly}\\jobheader{CNOGA}"
          "\\end{document}")
    client = fake_llm([cv])
    out = tailor_resume(client, "INSTRUCTIONS", "BASE", {"title": "iOS", "company": "Acme"})
    assert out == cv
    assert len(client.prompts) == 1


def test_tailor_resume_regenerates_on_violation(fake_llm):
    bad = ("\\documentclass{x}\\begin{document}"
           "\\jobheader{Applitools}\\jobheader{Check Point}"
           "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}")
    good = ("\\documentclass{x}\\begin{document}"
            "\\jobheader{Check Point}\\jobheader{Applitools}"
            "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}")
    client = fake_llm([bad, good])
    out = tailor_resume(client, "INSTRUCTIONS", "BASE", {"title": "iOS", "company": "Acme"})
    assert out == good
    assert len(client.prompts) == 2
    assert "CORRECTION REQUIRED" in client.prompts[1]
