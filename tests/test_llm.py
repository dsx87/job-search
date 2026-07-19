"""Request-contract, factory, breaker, and telemetry tests for the LLM providers."""
import json
import urllib.error
from types import SimpleNamespace

import pytest

# --- modules under test (repoint on migration) ---
from job_search.config import load_base_tex
from job_search.latex.tailor_render import extract_job_bullets
from job_search.llm.clients import (
    SCHEME_DEFAULT_BASE,
    AnthropicProvider,
    GeminiProvider,
    LLMClient,
    OpenAIProvider,
    build_provider,
)
from job_search.llm.cv_edits import CV_EDIT_SCHEMA, select_cv_bullets
from job_search.llm.eval import evaluate_job
from job_search.llm.facts import FACT_SCHEMA, _normalize_facts, extract_facts
from job_search.llm.tailor import CVValidationError, tailor_resume
from job_search.profile import EXPECTED_JOB_ORDER, validate_tailored_cv


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class _FakeProvider:
    """Stand-in for a provider. Returns canned values or raises queued
    exceptions, in order. Deliberately has no ``last_usage`` attribute until a
    test sets one, so the accumulation path's missing-usage tolerance is exercised."""

    def __init__(self, items, *, scheme="gemini", model="primary-model"):
        self.items = list(items)
        self.calls = 0
        self.scheme = scheme
        self.model = model

    def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(primary_items, fallback_items):
    return LLMClient(
        _FakeProvider(primary_items, scheme="gemini", model="gemini-2.5-flash"),
        _FakeProvider(fallback_items, scheme="openai", model="gpt-5.4-mini"),
    )


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


# =====================================================================
# Per-scheme request contracts
# =====================================================================


def test_gemini_request_uses_configured_model_base_header_and_low_thinking(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured.update(request=request, timeout=timeout)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = GeminiProvider(
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

    assert GeminiProvider("key", model="gemini-3.5-flash").generate("prompt") == "answer"


def test_gemini_25_default_disables_thinking_with_budget(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    GeminiProvider("key", model="gemini-2.5-flash").generate("prompt")

    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert captured["payload"]["generationConfig"]["temperature"] == 0.0


def test_gemini_unknown_model_omits_thinking_config(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    GeminiProvider("key", model="future-model").generate("prompt")

    assert "thinkingConfig" not in captured["payload"]["generationConfig"]


def test_openai_request_omits_temperature_and_uses_json_object(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = OpenAIProvider(
        "openai-key", model="gpt-5.4-mini", api_base="https://oai.example/v1/"
    )

    assert client.generate("prompt", json_mode=True) == "ok"

    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://oai.example/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer openai-key"
    assert payload["model"] == "gpt-5.4-mini"
    # gpt-5.4-mini rejects a non-default temperature — omitted by default.
    assert "temperature" not in payload
    assert payload["response_format"] == {"type": "json_object"}


def test_openai_sends_temperature_when_enabled(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    OpenAIProvider("k", model="qwen-plus", send_temperature=True).generate("p", temperature=0.0)

    assert captured["payload"]["temperature"] == 0.0


def test_anthropic_request_contract(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        return _Response({"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = AnthropicProvider(
        "anth-key", model="claude-haiku-4-5", api_base="https://anth.example/v1/", max_tokens=1234
    )

    assert client.generate("prompt") == "hi"

    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://anth.example/v1/messages"
    assert request.get_header("X-api-key") == "anth-key"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["max_tokens"] == 1234
    assert payload["messages"] == [{"role": "user", "content": "prompt"}]
    assert payload["temperature"] == 0.0


def test_anthropic_json_mode_is_prompt_driven_only(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _Response({"content": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    # The Messages API has no json_object mode — json_mode/response_schema are
    # accepted for interface parity but never sent on the wire.
    AnthropicProvider("k", model="claude-haiku-4-5").generate(
        "p", json_mode=True, response_schema={"type": "object"}
    )

    assert "response_format" not in captured["payload"]
    assert "responseSchema" not in captured["payload"]


def test_anthropic_response_skips_non_text_blocks(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({"content": [
            {"type": "thinking", "text": "hmm"},
            {"type": "text", "text": "answer"},
        ]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert AnthropicProvider("k", model="claude-haiku-4-5").generate("p") == "answer"


def test_provider_model_override_is_keyword_only():
    with pytest.raises(TypeError):
        GeminiProvider("key", "gemini-custom")
    with pytest.raises(TypeError):
        OpenAIProvider("key", "gpt-custom")
    with pytest.raises(TypeError):
        AnthropicProvider("key", "claude-custom")


# =====================================================================
# Shared retry helper (per provider)
# =====================================================================


def test_provider_retries_transient_then_succeeds(monkeypatch):
    import job_search.llm.clients as clients

    monkeypatch.setattr(clients.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert GeminiProvider("key", model="gemini-2.5-flash").generate("p") == "ok"
    assert calls["n"] == 2  # retried the 503 once, then succeeded


def test_provider_retry_raises_after_exhausting_attempts(monkeypatch):
    import job_search.llm.clients as clients

    monkeypatch.setattr(clients.time, "sleep", lambda _s: None)

    def urlopen(request, timeout):
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        GeminiProvider("key", model="gemini-2.5-flash").generate("p")


def test_provider_non_retryable_http_error_not_retried(monkeypatch):
    calls = {"n": 0}

    def urlopen(request, timeout):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        GeminiProvider("key", model="gemini-2.5-flash").generate("p")
    assert calls["n"] == 1  # 400 is not retryable


# =====================================================================
# Scheme factory
# =====================================================================


def test_build_provider_maps_scheme_to_class():
    assert isinstance(build_provider("gemini", api_key="k", model="m"), GeminiProvider)
    assert isinstance(build_provider("openai", api_key="k", model="m"), OpenAIProvider)
    assert isinstance(build_provider("anthropic", api_key="k", model="m"), AnthropicProvider)


def test_build_provider_normalizes_alias_and_case():
    assert isinstance(build_provider("GOOGLE", api_key="k", model="m"), GeminiProvider)
    assert isinstance(build_provider("  Gemini ", api_key="k", model="m"), GeminiProvider)


def test_build_provider_applies_scheme_default_base_for_blank():
    provider = build_provider("openai", api_key="k", model="m", api_base="")
    assert provider.api_base == SCHEME_DEFAULT_BASE["openai"]


def test_build_provider_uses_explicit_base_over_default():
    provider = build_provider("openai", api_key="k", model="m", api_base="https://groq.example/openai/v1/")
    assert provider.api_base == "https://groq.example/openai/v1"  # trailing slash normalized


def test_build_provider_unknown_scheme_raises():
    with pytest.raises(ValueError, match="supported schemes"):
        build_provider("mystery", api_key="k", model="m")


def test_llm_client_from_config_builds_primary_and_fallback():
    cfg = SimpleNamespace(
        llm_primary_scheme="gemini", llm_primary_model="gemini-2.5-flash",
        llm_primary_api_key="pk", llm_primary_api_base="",
        llm_fallback_scheme="openai", llm_fallback_model="gpt-5.4-mini",
        llm_fallback_api_key="fk", llm_fallback_api_base="",
    )
    client = LLMClient.from_config(cfg)
    assert isinstance(client.primary, GeminiProvider)
    assert isinstance(client.fallback, OpenAIProvider)
    assert client.primary.model == "gemini-2.5-flash"
    assert client.fallback.api_base == SCHEME_DEFAULT_BASE["openai"]


def test_llm_client_from_config_omits_fallback_without_key():
    cfg = SimpleNamespace(
        llm_primary_scheme="gemini", llm_primary_model="m", llm_primary_api_key="pk", llm_primary_api_base="",
        llm_fallback_scheme="openai", llm_fallback_model="m", llm_fallback_api_key="", llm_fallback_api_base="",
    )
    assert LLMClient.from_config(cfg).fallback is None


# =====================================================================
# Circuit-breaker + fallback (LLMClient)
# =====================================================================


def test_success_path_counts_primary():
    c = _client(["G"], [])
    assert c.generate("p") == "G"
    assert c._primary_calls == 1
    assert c._fallback_calls == 0


def test_single_breaker_failure_does_not_disable():
    # One transient 503 is served by the fallback but must NOT disable the
    # primary — it is below the breaker threshold (2).
    c = _client([_http_error(503)], ["F1"])
    assert c.generate("p") == "F1"
    assert c._primary_disabled is False
    assert c._consecutive_failures == 1
    assert c._fallback_calls == 1


def test_threshold_consecutive_failures_disables():
    c = _client([_http_error(503), _http_error(503)], ["F1", "F2"])
    assert c.generate("p1") == "F1"
    assert c._primary_disabled is False
    assert c.generate("p2") == "F2"
    assert c._primary_disabled is True
    assert "disabled mid-run" in c.usage_summary()
    # a third request skips the primary entirely
    c.fallback.items.append("F3")
    assert c.generate("p3") == "F3"
    assert c.primary.calls == 2  # primary was not called after being disabled
    assert c._fallback_calls == 3


def test_breaker_trips_on_429_too():
    c = _client([_http_error(429), _http_error(429)], ["F1", "F2"])
    assert c.generate("p1") == "F1"
    assert c._primary_disabled is False
    assert c.generate("p2") == "F2"
    assert c._primary_disabled is True


def test_success_resets_consecutive_failures():
    # 503 → success → 503: the success in the middle resets the streak, so the
    # second blip is failure #1 again and never trips the breaker.
    c = _client([_http_error(503), "G", _http_error(503)], ["F1", "F2"])
    assert c.generate("p1") == "F1"
    assert c._consecutive_failures == 1
    assert c.generate("p2") == "G"
    assert c._consecutive_failures == 0
    assert c.generate("p3") == "F2"
    assert c._consecutive_failures == 1
    assert c._primary_disabled is False


def test_non_breaker_http_error_falls_back_without_disabling():
    c = _client([_http_error(500), "G2"], ["F"])
    assert c.generate("p1") == "F"       # 500 not a breaker status → per-request fallback
    assert c._primary_disabled is False
    assert c._consecutive_failures == 0  # non-breaker errors don't count toward the breaker
    assert c.generate("p2") == "G2"      # next request hits the primary again
    assert c._primary_calls == 1
    assert c._fallback_calls == 1


def test_non_http_error_falls_back_without_disabling():
    c = _client([ValueError("boom"), "G2"], ["F"])
    assert c.generate("p1") == "F"
    assert c._primary_disabled is False
    assert c.generate("p2") == "G2"


def test_no_fallback_reraises_on_breaker_error():
    c = LLMClient(_FakeProvider([_http_error(429)]))  # no fallback
    with pytest.raises(RuntimeError):
        c.generate("p")


def test_no_fallback_reraises_original_non_breaker_error():
    c = LLMClient(_FakeProvider([_http_error(500)]))
    with pytest.raises(urllib.error.HTTPError):
        c.generate("p")


def test_no_fallback_reraises_non_http_error():
    c = LLMClient(_FakeProvider([ValueError("boom")]))
    with pytest.raises(ValueError):
        c.generate("p")


def test_fallback_receives_same_kwargs():
    calls = []

    class RecordingFallback:
        scheme = "openai"
        model = "gpt-5.4-mini"

        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            calls.append((prompt, temperature, json_mode))
            return "F"

    c = LLMClient(_FakeProvider([_http_error(429)]), RecordingFallback())
    assert c.generate("prompt", temperature=0.0, json_mode=True) == "F"
    assert calls == [("prompt", 0.0, True)]


def test_usage_summary_uses_scheme_and_model_labels(capsys):
    # A non-breaker 500 forces a per-request fallback log, which carries the labels.
    c = LLMClient(
        _FakeProvider([_http_error(500)], scheme="gemini", model="gemini-2.5-flash"),
        _FakeProvider(["F"], scheme="openai", model="gpt-5.4-mini"),
    )
    assert c.generate("p") == "F"
    output = capsys.readouterr().out
    assert "gemini/gemini-2.5-flash" in output
    assert "openai/gpt-5.4-mini" in output
    summary = c.usage_summary()
    assert "gemini/gemini-2.5-flash" in summary
    assert "openai/gpt-5.4-mini" in summary


def test_output_does_not_expose_api_keys(capsys):
    primary = _FakeProvider([_http_error(500)], scheme="gemini", model="gemini-2.5-flash")
    primary.api_key = "gemini-secret"
    fallback = _FakeProvider(["F"], scheme="openai", model="gpt-5.4-mini")
    fallback.api_key = "openai-secret"
    c = LLMClient(primary, fallback)

    assert c.generate("p") == "F"

    output = capsys.readouterr().out + c.usage_summary()
    assert "gemini-secret" not in output
    assert "openai-secret" not in output


def test_llm_client_threads_response_schema_to_primary():
    seen = []

    class RecordingPrimary:
        scheme = "gemini"
        model = "m"

        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            seen.append(response_schema)
            return "G"

    c = LLMClient(RecordingPrimary(), _FakeProvider(["F"]))
    schema = {"type": "object"}

    assert c.generate("p", response_schema=schema) == "G"
    assert seen == [schema]


def test_llm_client_threads_response_schema_to_fallback():
    seen = []

    class RecordingFallback:
        scheme = "openai"
        model = "m"

        def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
            seen.append(response_schema)
            return "F"

    c = LLMClient(_FakeProvider([_http_error(429)]), RecordingFallback())
    schema = {"type": "object"}

    assert c.generate("p", response_schema=schema) == "F"
    assert seen == [schema]


# =====================================================================
# Token telemetry — per-provider last_usage + LLMClient totals
# =====================================================================


def test_gemini_records_last_usage_from_usage_metadata(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 20,
                "thoughtsTokenCount": 7,
                "cachedContentTokenCount": 3,
            },
        })

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = GeminiProvider("key", model="gemini-2.5-flash")

    assert client.last_usage == {"input": 0, "output": 0, "thinking": 0, "cached": 0}
    assert client.generate("p") == "ok"
    assert client.last_usage == {"input": 100, "output": 20, "thinking": 7, "cached": 3}


def test_gemini_last_usage_zero_without_usage_metadata(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = GeminiProvider("key", model="gemini-2.5-flash")

    client.generate("p")
    assert client.last_usage == {"input": 0, "output": 0, "thinking": 0, "cached": 0}


def test_openai_records_last_usage_maps_reasoning_and_cached(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        })

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = OpenAIProvider("key", model="gpt-5.4-mini")

    assert client.last_usage == {"input": 0, "output": 0, "thinking": 0, "cached": 0}
    assert client.generate("p") == "ok"
    # reasoning_tokens → thinking; cached_tokens → cached.
    assert client.last_usage == {"input": 10, "output": 5, "thinking": 2, "cached": 4}


def test_openai_last_usage_without_usage_or_detail_blocks(monkeypatch):
    responses = iter([
        {"choices": [{"message": {"content": "ok"}}]},  # no usage block at all
        {  # usage present, no detail sub-blocks → thinking/cached default to 0
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2},
        },
    ])

    def urlopen(_request, timeout):
        return _Response(next(responses))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = OpenAIProvider("key", model="gpt-5.4-mini")

    client.generate("p")
    assert client.last_usage == {"input": 0, "output": 0, "thinking": 0, "cached": 0}

    client.generate("p")
    assert client.last_usage == {"input": 8, "output": 2, "thinking": 0, "cached": 0}


def test_anthropic_records_last_usage(monkeypatch):
    def urlopen(_request, timeout):
        return _Response({
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 30, "output_tokens": 12, "cache_read_input_tokens": 5},
        })

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = AnthropicProvider("key", model="claude-haiku-4-5")

    assert client.last_usage == {"input": 0, "output": 0, "thinking": 0, "cached": 0}
    assert client.generate("p") == "ok"
    assert client.last_usage == {"input": 30, "output": 12, "thinking": 0, "cached": 5}


def test_llm_client_token_usage_starts_zero_and_tolerates_missing_last_usage():
    # _FakeProvider has no last_usage attribute — accumulation must skip it, not crash.
    c = _client(["G"], ["F"])
    assert c.token_usage() == {"input": 0, "output": 0, "thinking": 0, "cached": 0}
    assert c.generate("p") == "G"
    assert c.token_usage() == {"input": 0, "output": 0, "thinking": 0, "cached": 0}


def test_llm_client_accumulates_primary_last_usage_across_calls():
    c = _client(["G1", "G2"], [])
    c.primary.last_usage = {"input": 100, "output": 20, "thinking": 5, "cached": 3}
    assert c.generate("p1") == "G1"
    assert c.token_usage() == {"input": 100, "output": 20, "thinking": 5, "cached": 3}

    c.primary.last_usage = {"input": 10, "output": 2, "thinking": 1, "cached": 0}
    assert c.generate("p2") == "G2"
    assert c.token_usage() == {"input": 110, "output": 22, "thinking": 6, "cached": 3}


def test_llm_client_accumulates_served_fallback_usage():
    c = _client([_http_error(429)], ["F"])
    c.fallback.last_usage = {"input": 7, "output": 3, "thinking": 0, "cached": 2}
    assert c.generate("p") == "F"
    # the served provider (fallback) is the one whose usage is accumulated
    assert c.token_usage() == {"input": 7, "output": 3, "thinking": 0, "cached": 2}


def test_token_usage_returns_a_copy():
    c = _client(["G"], [])
    c.primary.last_usage = {"input": 1, "output": 2, "thinking": 3, "cached": 4}
    c.generate("p")

    snapshot = c.token_usage()
    snapshot["input"] = 999
    assert c.token_usage() == {"input": 1, "output": 2, "thinking": 3, "cached": 4}


def test_usage_summary_reports_token_totals_without_dumping_dict():
    c = _client(["G"], [])
    c.primary.last_usage = {"input": 12345, "output": 6789, "thinking": 321, "cached": 44}
    c.generate("p")

    summary = c.usage_summary()
    assert "Tokens" in summary
    for number in ("12345", "6789", "321", "44"):
        assert number in summary
    assert "{'input'" not in summary


# =====================================================================
# audit order 5+: eval/tailor prompts + structured facts/edits
# (These use a fake client with .generate — provider-agnostic.)
# =====================================================================


class _RecordingClient:
    """Fake client that records prompts and every generate() kwarg.

    Unlike conftest's ``FakeLLM``, this accepts (and captures) the
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


def test_evaluate_job_skips_llm_for_non_english_posting():
    # A non-English, non-Israeli posting is rejected WITHOUT any LLM call —
    # the empty response list would make generate() raise if it were invoked.
    client = _RecordingClient([])
    result = evaluate_job(
        client,
        "CRIT",
        {
            "title": "iOS Entwickler",
            "company": "Acme",
            "description": (
                "Wir suchen einen erfahrenen iOS-Entwickler für unser Team in "
                "Berlin. Sie entwickeln und pflegen Funktionen für unsere mobilen "
                "Anwendungen mit Swift und SwiftUI im Büro vor Ort."
            ),
        },
    )
    assert client.prompts == []  # no fact-extraction call was made
    assert result["verdict"] == "nonfit"
    assert "english" in result["reason"].lower()
    assert set(result.keys()) == {"fit", "reason", "timezone_note", "verdict", "facts"}
    assert result["facts"]["work_arrangement"] == "unknown"  # well-formed default


def test_evaluate_job_calls_llm_for_english_posting():
    client = _RecordingClient([json.dumps(_VALID_FACTS)])
    evaluate_job(
        client,
        "CRIT",
        {
            "title": "iOS",
            "company": "Acme",
            "description": (
                "We are hiring a fully remote iOS engineer to build apps with "
                "Swift and SwiftUI for our users around the world."
            ),
        },
    )
    assert len(client.prompts) == 1  # English posting is not short-circuited


def test_evaluate_job_extracts_for_israeli_non_english_posting():
    # Israeli roles are exempt from the language gate, so extraction still runs
    # (we need the facts to judge office-attendance for a Hebrew Israeli post).
    client = _RecordingClient([json.dumps(_VALID_FACTS)])
    result = evaluate_job(
        client,
        "CRIT",
        {
            "title": "iOS",
            "company": "Acme",
            "location": "Tel Aviv",
            "description": "משרה מלאה למפתח iOS בכיר בתל אביב עם ניסיון רב ב-Swift.",
        },
    )
    assert len(client.prompts) == 1  # extraction ran despite the Hebrew text
    assert result["verdict"] != "nonfit" or "english" not in result["reason"].lower()


# =====================================================================
# audit order 7 — structured CV edits (select_cv_bullets) + tailor rewire
# =====================================================================


def test_select_cv_bullets_request_contract_and_selection():
    client = _RecordingClient(['{"jobs":[{"company":"Check Point","keep_bullets":[0,1]}]}'])

    selection = select_cv_bullets(
        client,
        load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": "We build iOS apps."},
    )

    # the CV-edit schema is threaded through to the model
    assert client.kwargs[0]["response_schema"] == CV_EDIT_SCHEMA
    # the prompt enumerates the company and at least one of its bullets
    assert "Check Point" in client.prompts[0]
    assert "Trusted Network Detection" in client.prompts[0]
    # the parsed selection maps the company to its kept bullet indices
    assert selection["Check Point"] == [0, 1]


def test_select_cv_bullets_non_json_returns_empty_selection():
    client = _RecordingClient(["not json"])

    selection = select_cv_bullets(
        client,
        load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": "desc"},
    )

    assert selection == {}


def test_select_cv_bullets_prompt_surfaces_late_restriction():
    long_desc = (
        "Overview of the role. "
        + ("iOS Swift work. " * 550)
        + " Eligibility: US residents only, no visa sponsorship."
    )
    # the restriction sits beyond the naive 7000-char prefix cut
    assert len(long_desc) > 7000
    assert "US residents only" not in long_desc[:7000]

    client = _RecordingClient(['{"jobs":[{"company":"Check Point","keep_bullets":[0]}]}'])
    select_cv_bullets(
        client,
        load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": long_desc},
    )

    assert "US residents only" in client.prompts[0]


def test_tailor_resume_renders_selected_bullets():
    client = _RecordingClient(['{"jobs":[{"company":"Check Point","keep_bullets":[0]}]}'])

    out = tailor_resume(
        client,
        "instr",
        load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": "d"},
    )

    assert validate_tailored_cv(out) == []
    for company in EXPECTED_JOB_ORDER:
        assert company in out
    rendered = {job["company"]: job for job in extract_job_bullets(out)}
    assert len(rendered["Check Point"]["bullets"]) == 1
    assert "((PHONE))" in out


def test_tailor_resume_falls_back_on_bad_selection():
    client = _RecordingClient(["garbage"])

    out = tailor_resume(
        client,
        "instr",
        load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": "d"},
    )

    assert validate_tailored_cv(out) == []
    jobs = extract_job_bullets(out)
    assert [job["company"] for job in jobs] == EXPECTED_JOB_ORDER
    assert [len(job["bullets"]) for job in jobs] == [6, 3, 3, 4]


def test_select_cv_bullets_survives_scalar_jobs_and_keep_bullets():
    # A fallback provider is schema-unconstrained: a scalar where a list is expected
    # must degrade to an empty selection, never raise (renderer then keeps all).
    from job_search.llm.cv_edits import _parse_selection

    assert _parse_selection('{"jobs": 5}') == {}
    assert _parse_selection('{"jobs": [{"company": "Check Point", "keep_bullets": 3}]}') == {
        "Check Point": []
    }
    client = _RecordingClient(['{"jobs": [{"company": "Check Point", "keep_bullets": 3}]}'])
    out = tailor_resume(client, "instr", load_base_tex(), {"title": "iOS", "company": "Acme", "description": "d"})
    assert validate_tailored_cv(out) == []
