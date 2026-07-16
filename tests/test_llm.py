"""Characterization and request-contract tests for the LLM clients."""
import json
import urllib.error

import pytest

# --- modules under test (repoint on migration) ---
from job_search.llm.clients import GeminiClient, LLMClient, QwenClient
from job_search.llm.eval import evaluate_job
from job_search.llm.tailor import CVValidationError, tailor_resume


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


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_gemini_request_uses_configured_model_base_header_and_low_thinking(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured.update(request=request, timeout=timeout)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = GeminiClient(
        "secret-key",
        model="gemini-3.5-flash",
        api_base="https://gemini.example/v1/models/",
    )

    assert client.generate("prompt", json_mode=True) == "ok"

    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://gemini.example/v1/models/gemini-3.5-flash:generateContent"
    assert "secret-key" not in request.full_url
    assert request.get_header("X-goog-api-key") == "secret-key"
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert "temperature" not in payload["generationConfig"]


def test_gemini_response_skips_non_text_parts(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({
            "candidates": [{
                "content": {"parts": [
                    {"thoughtSignature": "opaque"},
                    {"text": "answer"},
                ]},
                "finishReason": "STOP",
            }],
        })

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert GeminiClient("key", model="gemini-3.5-flash").generate("prompt") == "answer"


def test_gemini_25_override_disables_thinking_with_budget(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    GeminiClient("key", model="gemini-2.5-flash").generate("prompt")

    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 0
    }
    assert captured["payload"]["generationConfig"]["temperature"] == 0.0


def test_gemini_unknown_model_omits_thinking_config(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    GeminiClient("key", model="future-model").generate("prompt")

    assert "thinkingConfig" not in captured["payload"]["generationConfig"]


def test_qwen_request_uses_configured_model_and_base(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = QwenClient(
        "qwen-key",
        model="qwen-custom",
        api_base="https://qwen.example/openai/v1/",
    )

    assert client.generate("prompt", json_mode=True) == "ok"

    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://qwen.example/openai/v1/chat/completions"
    assert payload["model"] == "qwen-custom"
    assert payload["temperature"] == 0.0
    assert payload["response_format"] == {"type": "json_object"}


def test_qwen_fallback_keeps_requested_temperature():
    calls = []

    class RecordingQwen:
        def generate(self, prompt, temperature=0.0, json_mode=False):
            calls.append((prompt, temperature, json_mode))
            return "Q"

    client = LLMClient("g", "q", gemini_model="gemini-3.5-flash")
    client.gemini = _FakeModel([_http_error(429)])
    client.qwen = RecordingQwen()

    assert client.generate("prompt", temperature=0.0, json_mode=True) == "Q"
    assert calls == [("prompt", 0.0, True)]


def test_fallback_logs_and_usage_use_configured_model_names(capsys):
    client = LLMClient(
        "g",
        "q",
        gemini_model="gemini-custom",
        qwen_model="qwen-custom",
    )
    client.gemini = _FakeModel([_http_error(429)])
    client.qwen = _FakeModel(["Q"])

    assert client.generate("prompt") == "Q"

    output = capsys.readouterr().out
    assert "gemini-custom" in output
    assert "qwen-custom" in output
    summary = client.usage_summary()
    assert "gemini-custom" in summary
    assert "qwen-custom" in summary


def test_provider_configuration_overrides_are_keyword_only():
    with pytest.raises(TypeError):
        GeminiClient("key", "gemini-custom")
    with pytest.raises(TypeError):
        QwenClient("key", "qwen-custom")
    with pytest.raises(TypeError):
        LLMClient("g", "q", "gemini-custom")


def test_fallback_output_does_not_expose_api_keys(capsys):
    client = LLMClient("gemini-secret", "qwen-secret")
    client.gemini = _FakeModel([_http_error(429)])
    client.qwen = _FakeModel(["Q"])

    assert client.generate("prompt") == "Q"

    output = capsys.readouterr().out + client.usage_summary()
    assert "gemini-secret" not in output
    assert "qwen-secret" not in output


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


def test_tailor_resume_raises_when_violations_persist(fake_llm):
    bad_first = (
        "\\documentclass{x}\\begin{document}"
        "\\jobheader{Applitools}\\jobheader{Check Point}"
        "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}"
    )
    bad_second = (
        "\\documentclass{x}\\begin{document}"
        "\\jobheader{Check Point}\\jobheader{Applitools}"
        "\\jobheader{Shutterfly}\\end{document}"
    )
    client = fake_llm([bad_first, bad_second])

    with pytest.raises(CVValidationError) as raised:
        tailor_resume(client, "INSTRUCTIONS", "BASE", {"title": "iOS", "company": "Acme"})

    assert len(client.prompts) == 2
    assert isinstance(raised.value.violations, tuple)
    assert any("missing job" in violation for violation in raised.value.violations)
