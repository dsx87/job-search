"""Characterization and request-contract tests for the LLM clients."""
import json
import urllib.error

import pytest

# --- modules under test (repoint on migration) ---
from job_search.llm.clients import GeminiClient, LLMClient, QwenClient
from job_search.llm.eval import evaluate_job
from job_search.llm.facts import FACT_SCHEMA, _normalize_facts, extract_facts
from job_search.llm.tailor import CVValidationError, tailor_resume


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class _FakeModel:
    """Stand-in for GeminiClient/QwenClient. Returns canned values or raises
    queued exceptions, in order."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
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
        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
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


# --- audit order 5: eval/tailor prompts use section_aware_excerpt (Finding 11) ---
def test_extract_facts_prompt_surfaces_late_restriction():
    long_desc = (
        "Overview of the role. "
        + ("iOS Swift work. " * 400)
        + " Eligibility: US residents only, no visa sponsorship."
    )
    # the restriction sits beyond the naive 5000-char prefix cut
    assert len(long_desc) > 5000
    assert "US residents only" not in long_desc[:5000]

    client = _RecordingClient([json.dumps(_VALID_FACTS)])
    extract_facts(client, {"title": "iOS", "company": "Acme", "description": long_desc})

    assert "US residents only" in client.prompts[0]


def test_tailor_resume_prompt_surfaces_late_restriction(fake_llm):
    long_desc = (
        "Overview of the role. "
        + ("iOS Swift work. " * 550)
        + " Eligibility: US residents only, no visa sponsorship."
    )
    # the restriction sits beyond the naive 7000-char prefix cut
    assert len(long_desc) > 7000
    assert "US residents only" not in long_desc[:7000]

    cv = ("\\documentclass{x}\\begin{document}"
          "\\jobheader{Check Point}\\jobheader{Applitools}"
          "\\jobheader{Shutterfly}\\jobheader{CNOGA}\\end{document}")
    client = fake_llm([cv])
    tailor_resume(client, "INSTRUCTIONS", "BASE", {"title": "iOS", "company": "Acme", "description": long_desc})

    assert "US residents only" in client.prompts[0]


# =====================================================================
# audit order 6, part 1 — schema-constrained clients + structured facts
# =====================================================================


class _RecordingClient:
    """Fake client that records prompts and every generate() kwarg.

    Unlike conftest's ``FakeLLM``, this accepts (and captures) the new
    ``response_schema`` keyword, so facts/eval tests can assert that
    ``extract_facts`` threads ``FACT_SCHEMA`` through to the model.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []
        self.kwargs = []

    def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
        self.prompts.append(prompt)
        self.kwargs.append(
            {
                "temperature": temperature,
                "json_mode": json_mode,
                "response_schema": response_schema,
            }
        )
        if not self._responses:
            raise AssertionError("_RecordingClient ran out of canned responses")
        return self._responses.pop(0)


# A complete, valid facts payload the model might return (evidence in array form).
_VALID_FACTS = {
    "platform_focus": "ios_macos",
    "seniority": "senior",
    "employment_type": "full_time",
    "work_arrangement": "remote",
    "remote_geo_scope": "worldwide",
    "restricted_to_countries": [],
    "offers_sponsorship": "no",
    "authorization_blocker": "no",
    "office_days_4plus": "no",
    "industry_crypto_web3": "no",
    "requires_us_hours": "no",
    "evidence": [{"field": "platform_focus", "snippet": "iOS and Swift"}],
}

_ENUM_FIELDS = (
    "platform_focus",
    "seniority",
    "employment_type",
    "work_arrangement",
    "remote_geo_scope",
    "offers_sponsorship",
    "authorization_blocker",
    "office_days_4plus",
    "industry_crypto_web3",
    "requires_us_hours",
)

_ALL_FACT_FIELDS = _ENUM_FIELDS + ("restricted_to_countries", "evidence")


# --- A) schema-constrained clients ------------------------------------

def test_gemini_request_sets_response_schema_and_json_mime(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    # json_mode intentionally left False: a schema implies JSON output.
    GeminiClient("key", model="gemini-3.5-flash").generate(
        "p", response_schema={"type": "object"}
    )

    gen_cfg = captured["payload"]["generationConfig"]
    assert gen_cfg["responseSchema"] == {"type": "object"}
    assert gen_cfg["responseMimeType"] == "application/json"


def test_qwen_request_response_schema_forces_json_object(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    QwenClient("key", model="qwen-custom").generate(
        "p", response_schema={"type": "object"}
    )

    payload = captured["payload"]
    assert payload["response_format"] == {"type": "json_object"}
    # The schema itself is not part of the Qwen wire payload.
    assert "responseSchema" not in payload
    assert "response_schema" not in payload


def test_llm_client_threads_response_schema_to_gemini():
    seen = []

    class RecordingGemini:
        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            seen.append(response_schema)
            return "G"

    client = LLMClient("g", "q")
    client.gemini = RecordingGemini()
    schema = {"type": "object"}

    assert client.generate("p", response_schema=schema) == "G"
    assert seen == [schema]


def test_llm_client_threads_response_schema_to_qwen_on_fallback():
    seen = []

    class Boom:
        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            raise _http_error(429)

    class RecordingQwen:
        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            seen.append(response_schema)
            return "Q"

    client = LLMClient("g", "q", gemini_model="gemini-3.5-flash")
    client.gemini = Boom()
    client.qwen = RecordingQwen()
    schema = {"type": "object"}

    assert client.generate("p", response_schema=schema) == "Q"
    assert seen == [schema]


# --- B) FACT_SCHEMA / extract_facts / _normalize_facts ----------------

def test_fact_schema_declares_all_expected_properties():
    props = FACT_SCHEMA["properties"]
    for field_name in _ALL_FACT_FIELDS:
        assert field_name in props


def test_extract_facts_returns_normalized_dict_and_prompts_job():
    client = _RecordingClient([json.dumps(_VALID_FACTS)])

    facts = extract_facts(
        client,
        {
            "title": "iOS Engineer",
            "company": "Acme",
            "description": "We build iOS apps in Swift.",
        },
    )

    # every schema field is present after normalization
    for field_name in _ALL_FACT_FIELDS:
        assert field_name in facts

    assert facts["platform_focus"] == "ios_macos"
    assert facts["restricted_to_countries"] == []
    # evidence normalized from array form into a field->snippet mapping
    assert facts["evidence"] == {"platform_focus": "iOS and Swift"}

    # the prompt carries the job title + description
    assert "iOS Engineer" in client.prompts[0]
    assert "We build iOS apps in Swift." in client.prompts[0]


def test_extract_facts_passes_fact_schema_to_generate():
    client = _RecordingClient([json.dumps(_VALID_FACTS)])
    extract_facts(client, {"title": "iOS", "company": "Acme", "description": "desc"})
    assert client.kwargs[0]["response_schema"] == FACT_SCHEMA


def test_normalize_facts_missing_fields_default_to_unknown():
    out = _normalize_facts({})
    for field_name in _ENUM_FIELDS:
        assert out[field_name] == "unknown"
    assert out["restricted_to_countries"] == []
    assert out["evidence"] == {}


def test_normalize_facts_invalid_enum_becomes_unknown():
    out = _normalize_facts(
        {"platform_focus": "banana", "seniority": 123, "work_arrangement": None}
    )
    assert out["platform_focus"] == "unknown"
    assert out["seniority"] == "unknown"
    assert out["work_arrangement"] == "unknown"


def test_normalize_facts_valid_enum_preserved():
    out = _normalize_facts({"platform_focus": "ios_macos", "work_arrangement": "remote"})
    assert out["platform_focus"] == "ios_macos"
    assert out["work_arrangement"] == "remote"


def test_normalize_facts_evidence_array_becomes_dict():
    out = _normalize_facts(
        {"evidence": [{"field": "seniority", "snippet": "Senior iOS Engineer"}]}
    )
    assert out["evidence"] == {"seniority": "Senior iOS Engineer"}


def test_normalize_facts_evidence_dict_passthrough():
    out = _normalize_facts({"evidence": {"seniority": "Senior iOS Engineer"}})
    assert out["evidence"] == {"seniority": "Senior iOS Engineer"}


def test_normalize_facts_restricted_countries_uppercased():
    out = _normalize_facts({"restricted_to_countries": ["us", "Canada"]})
    assert out["restricted_to_countries"] == ["US", "CANADA"]


def test_normalize_facts_garbage_input_is_all_unknown():
    for junk in (None, "nope", 42, ["a", "b"]):
        out = _normalize_facts(junk)
        for field_name in _ENUM_FIELDS:
            assert out[field_name] == "unknown"
        assert out["restricted_to_countries"] == []
        assert out["evidence"] == {}


# --- D) evaluate_job rewired to facts + policy -------------------------

def test_evaluate_job_fit_case_end_to_end():
    client = _RecordingClient([json.dumps(_VALID_FACTS)])

    result = evaluate_job(
        client,
        "CRIT",
        {"title": "iOS", "company": "Acme", "description": "Fully remote, worldwide."},
    )

    assert set(result.keys()) == {"fit", "reason", "timezone_note", "verdict", "facts"}
    assert result["fit"] is True
    assert result["verdict"] == "fit"
    assert isinstance(result["reason"], str) and result["reason"]
    assert result["timezone_note"] is None
    assert result["facts"]["work_arrangement"] == "remote"


def test_evaluate_job_nonfit_case_end_to_end():
    facts_payload = {
        **_VALID_FACTS,
        "industry_crypto_web3": "yes",
        "evidence": [{"field": "industry_crypto_web3", "snippet": "web3 startup"}],
    }
    client = _RecordingClient([json.dumps(facts_payload)])

    result = evaluate_job(
        client,
        "CRIT",
        {
            "title": "iOS",
            "company": "Acme",
            "description": "We are a web3 startup building wallets.",
        },
    )

    assert result["fit"] is False
    assert result["verdict"] == "nonfit"
    assert result["facts"]["industry_crypto_web3"] == "yes"
