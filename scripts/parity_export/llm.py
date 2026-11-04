"""LLM area: request shapes, response parsing, retry, breaker and both together.

Error kinds are short stable strings derived from the engine's exception
(first match wins; an unmapped exception stops the export so a new engine
error cannot slip into the goldens unnamed):

    urllib.error.HTTPError                         -> "http_status" (+ "status")
    urllib.error.URLError (not HTTPError)          -> "transport"
    TimeoutError / socket.timeout                  -> "timeout"
    RuntimeError "Anthropic stopped with stop_reason=refusal"     -> "refusal"
    RuntimeError "Anthropic stopped with stop_reason=max_tokens"  -> "max_tokens"
    RuntimeError "... returned no content" / "no text content"    -> "no_text"
    RuntimeError "Primary (...) unavailable ... no fallback"      -> "unavailable"
    KeyError / IndexError / TypeError / ValueError -> "invalid_response"
        (a body missing an expected field, or not JSON; the breaker scenarios
        use ValueError as the generic non-HTTP primary error, as the engine tests do)
"""
import contextlib
import inspect
import io
import json
import os
import time
import urllib.error
import urllib.request
from unittest import mock

from job_search.llm import clients

# Read by job_search.config at import; a set value would silently change the goldens.
_ENGINE_ENV_OVERRIDES = ("LLM_BREAKER_THRESHOLD", "ANTHROPIC_MAX_TOKENS")

KEYS = {"gemini": "test-key-gemini", "openai": "test-key-openai", "anthropic": "test-key-anthropic"}

GEMINI_3 = "gemini-3-flash-preview"
GEMINI_25 = "gemini-2.5-flash-lite"
OPENAI_MODEL = "gpt-5.4-mini"
ANTHROPIC_MODEL = "claude-sonnet-5-5"

# Property order is deliberately not alphabetical at every level.
NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["apply", "skip", "unknown"]},
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "score": {"type": "integer"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "source": {
                        "type": "object",
                        "properties": {"section": {"type": "string"}, "line": {"type": "integer"}},
                    },
                },
            },
        },
        "summary": {"type": "string"},
    },
}


def _error_kind(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return {"error": "http_status", "status": exc.code}
    if isinstance(exc, urllib.error.URLError):
        return {"error": "transport"}
    if isinstance(exc, TimeoutError):
        return {"error": "timeout"}
    if isinstance(exc, RuntimeError):
        message = str(exc)
        if message.startswith("Anthropic stopped with stop_reason=refusal"):
            return {"error": "refusal"}
        if message.startswith("Anthropic stopped with stop_reason=max_tokens"):
            return {"error": "max_tokens"}
        if "returned no content" in message or "returned no text content" in message:
            return {"error": "no_text"}
        if " unavailable (" in message and "no fallback configured" in message:
            return {"error": "unavailable"}
    if isinstance(exc, (KeyError, IndexError, TypeError, ValueError)):
        return {"error": "invalid_response"}
    raise AssertionError(f"unmapped engine exception: {type(exc).__name__}: {exc}")


def _outcome(call):
    try:
        return {"text": call()}
    except Exception as exc:  # every engine error is mapped or stops the export
        return _error_kind(exc)


def _http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, "err", {}, io.BytesIO(b'{"error":"scripted"}'))


def _scripted_error(item):
    """A fresh exception per raise: the engine caches the body and edits msg on HTTPError."""
    if isinstance(item, int):
        return _http_error(item)
    if item == "transport":
        return urllib.error.URLError("connection refused")
    if item == "timeout":
        return TimeoutError("timed out")
    if item == "value_error":
        return ValueError("boom")
    raise AssertionError(f"unknown scripted item {item!r}")


def _provider(scheme, model, base="", auth_mode="bearer", send_temperature=False):
    opts = {}
    if scheme == "openai":
        opts = {"auth_mode": auth_mode, "send_temperature": send_temperature}
    return clients.build_provider(scheme, api_key=KEYS[scheme], model=model, api_base=base, **opts)


def _success_body(scheme, text):
    if scheme == "gemini":
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    if scheme == "openai":
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


class _Response:
    def __init__(self, body):
        self._data = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._data


# ── requests.json ─────────────────────────────────────────────────────────────
_REQUEST_CONFIGS = [
    {"name": "gemini3", "scheme": "gemini", "model": GEMINI_3, "base_url": "", "auth_mode": "key", "send_temperature": False},
    {"name": "gemini25", "scheme": "gemini", "model": GEMINI_25, "base_url": "", "auth_mode": "key", "send_temperature": False},
    {"name": "openai_bearer", "scheme": "openai", "model": OPENAI_MODEL, "base_url": "", "auth_mode": "bearer", "send_temperature": False},
    {"name": "openai_bearer_temperature", "scheme": "openai", "model": "llama-4-scout", "base_url": "https://api.groq.com/openai/v1", "auth_mode": "bearer", "send_temperature": True},
    {"name": "openai_none_custom_base", "scheme": "openai", "model": "smollm2:135m", "base_url": "http://127.0.0.1:11434/v1", "auth_mode": "none", "send_temperature": False},
    {"name": "openai_none_custom_base_temperature", "scheme": "openai", "model": "local-model", "base_url": "http://127.0.0.1:1234/v1/", "auth_mode": "none", "send_temperature": True},
    {"name": "anthropic", "scheme": "anthropic", "model": ANTHROPIC_MODEL, "base_url": "", "auth_mode": "key", "send_temperature": False},
]

_REQUEST_VARIANTS = [
    {"name": "plain", "prompt": "Summarise this posting: iOS engineer in Zürich — Swift, SwiftUI.", "temperature": 0.7, "json_mode": False, "response_schema": None},
    {"name": "json_mode", "prompt": "Rate the fit of this job from 0 to 10 and give one reason.", "temperature": 0.0, "json_mode": True, "response_schema": None},
    {"name": "schema", "prompt": "Assess this job against the candidate profile.", "temperature": 0.2, "json_mode": True, "response_schema": NESTED_SCHEMA},
]


def _requests():
    default_timeout = inspect.signature(clients._post_json_with_retry).parameters["timeout"].default
    cases = []
    for config in _REQUEST_CONFIGS:
        for variant in _REQUEST_VARIANTS:
            captured = []

            def record(url, payload, headers, *, timeout=default_timeout, label="", _scheme=config["scheme"]):
                captured.append({
                    "method": "POST",
                    "url": url,
                    "headers": dict(headers),
                    "body": json.loads(json.dumps(payload)),
                    "timeout": timeout,
                })
                return _success_body(_scheme, "ok")

            schema = variant["response_schema"]
            auth_mode = "bearer" if config["auth_mode"] == "key" else config["auth_mode"]
            provider = _provider(config["scheme"], config["model"], config["base_url"], auth_mode, config["send_temperature"])
            with mock.patch.object(clients, "_post_json_with_retry", record):
                provider.generate(
                    variant["prompt"],
                    temperature=variant["temperature"],
                    json_mode=variant["json_mode"],
                    response_schema=schema,
                )
            assert len(captured) == 1
            cases.append({
                "name": f"{config['name']}_{variant['name']}",
                "inputs": {
                    "scheme": config["scheme"],
                    "model": config["model"],
                    "base_url": config["base_url"],
                    "auth_mode": config["auth_mode"],
                    "send_temperature": config["send_temperature"],
                    "prompt": variant["prompt"],
                    "temperature": variant["temperature"],
                    "json_mode": variant["json_mode"],
                    "response_schema": None if schema is None else json.dumps(schema, ensure_ascii=False),
                },
                "request": captured[0],
            })
    return cases


# ── responses.json ────────────────────────────────────────────────────────────
_RESPONSE_CASES = [
    ("gemini_text", "gemini", GEMINI_3, {
        "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 15, "totalTokenCount": 135},
    }),
    ("gemini_thinking", "gemini", GEMINI_3, {
        "candidates": [{"content": {"parts": [{"text": "Thought through"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 40, "thoughtsTokenCount": 310},
    }),
    ("gemini_cached", "gemini", GEMINI_25, {
        "candidates": [{"content": {"parts": [{"text": "From cache"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 5000, "candidatesTokenCount": 30, "cachedContentTokenCount": 4096},
    }),
    ("gemini_skips_non_text_parts", "gemini", GEMINI_3, {
        "candidates": [{"content": {"parts": [{"thought": True}, {"text": ""}, {"text": "Second part"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
    }),
    ("gemini_empty_text", "gemini", GEMINI_3, {
        "candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 0},
    }),
    ("gemini_no_parts", "gemini", GEMINI_3, {
        "candidates": [{"finishReason": "SAFETY"}],
        "usageMetadata": {"promptTokenCount": 10},
    }),
    ("gemini_missing_usage", "gemini", GEMINI_3, {
        "candidates": [{"content": {"parts": [{"text": "No usage"}]}, "finishReason": "STOP"}],
    }),
    ("gemini_no_candidates", "gemini", GEMINI_3, {
        "usageMetadata": {"promptTokenCount": 10},
    }),
    ("openai_text", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135},
    }),
    ("openai_reasoning", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": "Reasoned"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 350, "completion_tokens_details": {"reasoning_tokens": 310}},
    }),
    ("openai_cached", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": "From cache"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5000, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 4096}},
    }),
    ("openai_empty_content", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }),
    ("openai_null_content", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": None, "refusal": "I can't help with that."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }),
    ("openai_missing_usage", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": "No usage"}, "finish_reason": "stop"}],
    }),
    ("openai_null_detail_blocks", "openai", OPENAI_MODEL, {
        "choices": [{"message": {"content": "Null details"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "prompt_tokens_details": None, "completion_tokens_details": None},
    }),
    ("openai_no_choices", "openai", OPENAI_MODEL, {
        "choices": [],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }),
    ("anthropic_text", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": "Hello"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 15},
    }),
    ("anthropic_skips_thinking_block", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "thinking", "thinking": "Let me think.", "signature": "sig"}, {"type": "text", "text": "After thinking"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 200, "output_tokens": 400},
    }),
    ("anthropic_cached", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": "From cache"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 30, "cache_read_input_tokens": 4096, "cache_creation_input_tokens": 1024},
    }),
    ("anthropic_refusal", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": "Partial"}], "stop_reason": "refusal",
        "stop_details": {"category": "cyber"},
        "usage": {"input_tokens": 80, "output_tokens": 4},
    }),
    ("anthropic_max_tokens", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": "Truncated answ"}], "stop_reason": "max_tokens",
        "usage": {"input_tokens": 80, "output_tokens": 16000},
    }),
    ("anthropic_empty_text", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": ""}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }),
    ("anthropic_no_content", "anthropic", ANTHROPIC_MODEL, {
        "content": [], "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }),
    ("anthropic_missing_usage", "anthropic", ANTHROPIC_MODEL, {
        "content": [{"type": "text", "text": "No usage"}], "stop_reason": "end_turn",
    }),
]


def _responses():
    cases = []
    for name, scheme, model, body in _RESPONSE_CASES:
        provider = _provider(scheme, model)
        with mock.patch.object(clients, "_post_json_with_retry", lambda *_a, _body=body, **_k: json.loads(json.dumps(_body))):
            outcome = _outcome(lambda: provider.generate("p"))
        cases.append({
            "name": name,
            "scheme": scheme,
            "model": model,
            "body": body,
            "outcome": outcome,
            # Set before the body is validated, so error outcomes still carry usage.
            "usage": dict(provider.last_usage),
        })
    return cases


# ── urlopen/sleep harness shared by retry.json and composed.json ──────────────
class _Network:
    """Scripted urlopen: one queue per URL suffix; 200 answers with that route's text."""

    def __init__(self, routes):
        self.routes = {suffix: {"scheme": scheme, "text": text, "queue": list(seq), "calls": 0}
                       for suffix, (scheme, text, seq) in routes.items()}
        self.sleeps = []

    def urlopen(self, req, timeout=None):
        route = next(r for suffix, r in self.routes.items() if req.full_url.endswith(suffix))
        route["calls"] += 1
        item = route["queue"].pop(0)
        if item == 200:
            return _Response(_success_body(route["scheme"], route["text"]))
        raise _scripted_error(item)

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    @contextlib.contextmanager
    def patched(self):
        with mock.patch.object(urllib.request, "urlopen", self.urlopen), mock.patch.object(time, "sleep", self.sleep):
            yield

    def assert_drained(self):
        for suffix, route in self.routes.items():
            assert not route["queue"], f"unused scripted items for {suffix}: {route['queue']}"


# ── retry.json ────────────────────────────────────────────────────────────────
_RETRY_SEQUENCES = [
    ("ok", [200]),
    ("429_then_ok", [429, 200]),
    ("429_429_then_ok", [429, 429, 200]),
    ("429_x3", [429, 429, 429]),
    ("500_then_ok", [500, 200]),
    ("502_then_ok", [502, 200]),
    ("503_then_ok", [503, 200]),
    ("504_then_ok", [504, 200]),
    ("529_then_ok", [529, 200]),
    ("503_x3", [503, 503, 503]),
    ("400_not_retried", [400]),
    ("401_not_retried", [401]),
    ("403_not_retried", [403]),
    ("404_not_retried", [404]),
    ("429_then_400", [429, 400]),
    ("transport_then_ok", ["transport", 200]),
    ("transport_x3", ["transport", "transport", "transport"]),
    ("timeout_then_ok", ["timeout", 200]),
    ("timeout_x3", ["timeout", "timeout", "timeout"]),
]


def _retry():
    cases = []
    for name, sequence in _RETRY_SEQUENCES:
        net = _Network({":generateContent": ("gemini", "ok", sequence)})
        provider = _provider("gemini", GEMINI_3)
        with net.patched():
            outcome = _outcome(lambda: provider.generate("p"))
        net.assert_drained()
        cases.append({
            "name": name,
            "scheme": "gemini",
            "sequence": sequence,
            "http_calls": net.routes[":generateContent"]["calls"],
            "sleeps": net.sleeps,
            "outcome": outcome,
        })
    return cases


# ── breaker.json ──────────────────────────────────────────────────────────────
class _FakeProvider:
    def __init__(self, items, *, scheme, model):
        self.items = list(items)
        self.calls = 0
        self.scheme = scheme
        self.model = model

    def generate(self, prompt, temperature=0.0, json_mode=False, response_schema=None):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, str) and item.startswith("text:"):
            return item[len("text:"):]
        raise _scripted_error(item)


# (name, source test or "extra", primary script, fallback script or None, requests, engine_only)
# A "text:<role>" item answers; anything else raises (see _scripted_error).
_BREAKER_SCENARIOS = [
    ("success_counts_primary", "test_success_path_counts_primary", ["text:primary"], [], 1, False),
    ("single_503_does_not_disable", "test_single_breaker_failure_does_not_disable", [503], ["text:fallback"], 1, False),
    ("two_503_disable", "test_threshold_consecutive_failures_disables", [503, 503], ["text:fallback"] * 3, 3, False),
    ("two_429_disable", "test_breaker_trips_on_429_too", [429, 429], ["text:fallback"] * 2, 2, False),
    ("two_529_disable", "extra", [529, 529], ["text:fallback"] * 3, 3, False),
    ("success_resets_counter", "test_success_resets_consecutive_failures", [503, "text:primary", 503], ["text:fallback"] * 2, 3, False),
    ("500_falls_back_without_counting", "test_non_breaker_http_error_falls_back_without_disabling", [500, "text:primary"], ["text:fallback"], 2, False),
    ("non_http_error_falls_back", "test_non_http_error_falls_back_without_disabling", ["value_error", "text:primary"], ["text:fallback"], 2, False),
    ("transport_error_falls_back", "extra", ["transport", "text:primary"], ["text:fallback"], 2, False),
    ("no_fallback_429_unavailable", "test_no_fallback_reraises_on_breaker_error", [429], None, 1, False),
    ("no_fallback_429_x2_then_disabled", "extra", [429, 429], None, 3, False),
    ("no_fallback_500_rethrows", "test_no_fallback_reraises_original_non_breaker_error", [500], None, 1, False),
    ("no_fallback_non_http_rethrows", "test_no_fallback_reraises_non_http_error", ["value_error"], None, 1, False),
    ("fallback_gets_same_request", "test_fallback_receives_same_kwargs", [429], ["text:fallback"], 1, False),
    ("404_disables_at_once", "test_model_rejected_404_disables_primary_after_one_loud_message", [404], ["text:fallback"] * 2, 2, False),
    ("404_after_one_breaker_failure", "extra", [503, 404], ["text:fallback"] * 3, 3, False),
    ("400_falls_back_without_disabling", "test_request_rejected_400_falls_back_without_disabling_the_primary", [400, "text:primary"], ["text:fallback"], 2, False),
    ("400_twice", "test_request_rejection_is_reported_only_once_per_run", [400, 400], ["text:fallback"] * 2, 2, False),
    ("no_fallback_400_rethrows", "test_request_rejected_without_fallback_reraises_the_original_error", [400], None, 1, False),
    ("no_fallback_404_unavailable", "test_model_rejected_without_fallback_raises_with_the_cause", [404], None, 2, False),
    ("404_reason_recorded", "test_model_rejected_reason_shows_in_usage_summary", [404], ["text:fallback"], 1, False),
    ("fallback_error_propagates", "extra", [503], [500], 1, False),
    ("fallback_error_after_400", "extra", [400], ["transport"], 1, False),
    ("401_falls_back_per_request", "extra", [401, "text:primary"], ["text:fallback"], 2, True),
    ("403_falls_back_per_request", "extra", [403, "text:primary"], ["text:fallback"], 2, True),
    ("no_fallback_401_rethrows", "extra", [401], None, 1, True),
    ("no_fallback_403_rethrows", "extra", [403], None, 1, True),
]


def _answered_by(outcome):
    text = outcome.get("text")
    return text if text in ("primary", "fallback") else "none"


def _breaker():
    scenarios = []
    for name, source, primary_items, fallback_items, n_requests, engine_only in _BREAKER_SCENARIOS:
        primary = _FakeProvider(primary_items, scheme="gemini", model="primary-model")
        fallback = None if fallback_items is None else _FakeProvider(fallback_items, scheme="openai", model="fallback-model")
        client = clients.LLMClient(primary, fallback)
        requests = []
        for _ in range(n_requests):
            outcome = _outcome(lambda: client.generate("p", temperature=0.0, json_mode=True))
            requests.append({
                "answered_by": _answered_by(outcome),
                "error": {k: v for k, v in outcome.items() if k != "text"} or None,
                "primary_calls": primary.calls,
                "fallback_calls": 0 if fallback is None else fallback.calls,
                "primary_failures": client._consecutive_failures,
                "primary_disabled": client._primary_disabled,
            })
        assert not primary.items and (fallback is None or not fallback.items), name
        scenarios.append({
            "name": name,
            "source": source,
            "engine_only": engine_only,
            "primary": primary_items,
            "fallback": fallback_items,
            "requests": requests,
        })
    return scenarios


# ── composed.json ─────────────────────────────────────────────────────────────
# (name, primary urlopen sequence, fallback sequence or None, requests)
_COMPOSED_CASES = [
    ("429_x3_then_ok", [429, 429, 429, 200], [200], 2),
    ("429_429_200", [429, 429, 200], [], 1),
    ("503_x6", [503] * 6, [200] * 3, 3),
    ("529_x6_no_fallback", [529] * 6, None, 3),
    ("500_x3_then_ok", [500, 500, 500, 200], [200], 2),
    ("404", [404], [200, 200], 2),
    ("400_then_ok", [400, 200], [200], 2),
    ("timeout_x3_then_ok", ["timeout"] * 3 + [200], [200], 2),
    ("fallback_429_x3", [503, 503, 503], [429, 429, 429], 1),
]


def _composed():
    cases = []
    for name, primary_seq, fallback_seq, n_requests in _COMPOSED_CASES:
        routes = {":generateContent": ("gemini", "primary", primary_seq)}
        if fallback_seq is not None:
            routes["/chat/completions"] = ("openai", "fallback", fallback_seq)
        net = _Network(routes)
        primary = _provider("gemini", GEMINI_3)
        fallback = None if fallback_seq is None else _provider("openai", OPENAI_MODEL)
        client = clients.LLMClient(primary, fallback)
        models = {"primary": GEMINI_3, "fallback": OPENAI_MODEL}
        requests = []
        with net.patched():
            for _ in range(n_requests):
                before = {suffix: route["calls"] for suffix, route in net.routes.items()}
                sleeps_before = len(net.sleeps)
                outcome = _outcome(lambda: client.generate("p"))
                calls = {suffix: route["calls"] - before[suffix] for suffix, route in net.routes.items()}
                requests.append({
                    "answered_by": _answered_by(outcome),
                    "answering_model": models.get(outcome.get("text")),
                    "error": {k: v for k, v in outcome.items() if k != "text"} or None,
                    "primary_http_calls": calls[":generateContent"],
                    "fallback_http_calls": calls.get("/chat/completions", 0),
                    "sleeps": net.sleeps[sleeps_before:],
                    "primary_failures": client._consecutive_failures,
                    "primary_disabled": client._primary_disabled,
                })
        net.assert_drained()
        cases.append({
            "name": name,
            "primary": {"scheme": "gemini", "model": GEMINI_3, "sequence": primary_seq},
            "fallback": None if fallback_seq is None else {"scheme": "openai", "model": OPENAI_MODEL, "sequence": fallback_seq},
            "requests": requests,
        })
    return cases


def export():
    overrides = [name for name in _ENGINE_ENV_OVERRIDES if name in os.environ]
    if overrides:
        raise SystemExit(f"unset {', '.join(overrides)}: the goldens must use the engine defaults")
    # The engine prints retry and breaker lines with timestamps; keep them out of the CLI output.
    with contextlib.redirect_stdout(io.StringIO()):
        return {
            "requests.json": _requests(),
            "responses.json": _responses(),
            "retry.json": _retry(),
            "breaker.json": _breaker(),
            "composed.json": _composed(),
        }
