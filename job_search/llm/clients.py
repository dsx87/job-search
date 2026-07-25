"""Generic scheme-based LLM providers with a hardened circuit-breaker.

A *provider* is a wire-protocol scheme (``gemini`` | ``openai`` | ``anthropic``)
plus a model, an API key, and an optional base URL. Switching providers — now or
later — is a config edit, never a code change (see :func:`build_provider`).

``LLMClient`` holds a primary provider and an optional fallback. Each provider
retries transient errors internally (:func:`_post_json_with_retry`); the client's
breaker disables the primary for the rest of the run only after
``LLM_BREAKER_THRESHOLD`` consecutive post-retry 429/503s — so a single transient
blip is retried and recovers instead of dumping the whole run onto the fallback.

Every provider implements
``generate(prompt, temperature=0.0, json_mode=False, response_schema=None) -> str``
and exposes ``.last_usage`` (``{input, output, thinking, cached}``), ``.model``,
and ``.scheme``. JSON enforcement is best-effort per scheme (documented below).

Thread-safe: the eval/tailor stages call ``generate()`` from worker threads, and
the breaker state is guarded by ``self._lock``.
"""
import datetime
import json
import threading
import time
import urllib.error
import urllib.request

from ..config import (
    ANTHROPIC_MAX_TOKENS,
    LLM_BREAKER_THRESHOLD,
    LLM_CIRCUIT_BREAK_STATUS,
    LLM_FALLBACK_MODEL,
    LLM_MODEL_REJECT_STATUS,
    LLM_MODEL_SHUTDOWN_DATES,
    LLM_MODEL_SHUTDOWN_WARN_DAYS,
    LLM_PRIMARY_MODEL,
    LLM_RETRY_BACKOFF,
    RETRYABLE_STATUS,
)

_ZERO_USAGE = {"input": 0, "output": 0, "thinking": 0, "cached": 0}

# Default API base per scheme. A provider constructed with a blank api_base uses
# its scheme's entry here; the factory relies on the same map for resolution.
SCHEME_DEFAULT_BASE = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

# Scheme aliases → canonical name.
_SCHEME_ALIASES = {"google": "gemini"}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def model_shutdown_warning(model: str, today=None) -> str:
    """Return a one-line warning when ``model`` has an announced shutdown date near.

    Empty string when the model has no recorded date or the date is further out
    than ``LLM_MODEL_SHUTDOWN_WARN_DAYS``. Past the date the wording changes to
    past-tense, because at that point the provider may already be answering 404
    (which :class:`LLMClient` reports separately, but only once a call is made).
    """
    raw = LLM_MODEL_SHUTDOWN_DATES.get((model or "").strip())
    if not raw:
        return ""
    try:
        shutdown = datetime.date.fromisoformat(raw)
    except ValueError:
        return ""
    today = today or datetime.date.today()
    days = (shutdown - today).days
    if days > LLM_MODEL_SHUTDOWN_WARN_DAYS:
        return ""
    if days < 0:
        return (
            f"[LLM] {model} passed its announced shutdown date ({raw}) "
            f"{-days} day(s) ago — set LLM_PRIMARY_MODEL to a supported model."
        )
    return (
        f"[LLM] {model} is scheduled for shutdown on {raw} ({days} day(s) away) — "
        f"plan the LLM_PRIMARY_MODEL migration."
    )


def _post_json_with_retry(url, payload, headers, *, timeout=90, label=""):
    """POST a JSON body, retrying transient errors, and return the decoded reply.

    Retries HTTPErrors whose code is in ``RETRYABLE_STATUS`` and transient
    network/timeout errors (``URLError``/``TimeoutError``), sleeping the
    ``LLM_RETRY_BACKOFF`` delay between attempts. The final attempt re-raises, so
    a genuinely-down endpoint surfaces its HTTPError to ``LLMClient`` (whose
    breaker decides whether to disable the primary). ``HTTPError`` must be caught
    before ``URLError`` — it is a subclass.
    """
    data = json.dumps(payload).encode()
    delays = LLM_RETRY_BACKOFF
    attempts = len(delays)
    for attempt, delay in enumerate(delays, 1):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise
            print(
                f"    {label} transient error {exc.code} — waiting {delay}s "
                f"(attempt {attempt}/{attempts})...",
                flush=True,
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise
            reason = getattr(exc, "reason", exc)
            print(
                f"    {label} transient network error ({reason}) — waiting {delay}s "
                f"(attempt {attempt}/{attempts})...",
                flush=True,
            )
        time.sleep(delay)


# ── Usage mappers (per scheme → the common {input, output, thinking, cached}) ──
def _gemini_usage(result):
    meta = result.get("usageMetadata") or {}
    return {
        "input": _int(meta.get("promptTokenCount")),
        "output": _int(meta.get("candidatesTokenCount")),
        "thinking": _int(meta.get("thoughtsTokenCount")),
        "cached": _int(meta.get("cachedContentTokenCount")),
    }


def _openai_usage(result):
    usage = result.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "input": _int(usage.get("prompt_tokens")),
        "output": _int(usage.get("completion_tokens")),
        # Reasoning models report thinking under completion_tokens_details.
        "thinking": _int(completion_details.get("reasoning_tokens")),
        "cached": _int(prompt_details.get("cached_tokens")),
    }


def _anthropic_usage(result):
    usage = result.get("usage") or {}
    return {
        "input": _int(usage.get("input_tokens")),
        "output": _int(usage.get("output_tokens")),
        "thinking": 0,
        "cached": _int(usage.get("cache_read_input_tokens")),
    }


# ── Providers — one uniform interface, three schemes ──────────────────────────
class GeminiProvider:
    """Scheme ``gemini`` (alias ``google``): Google ``:generateContent``.

    Model-conditional ``thinkingConfig`` (2.5 → ``thinkingBudget: 0`` + temperature;
    3.x → ``thinkingLevel: low``). Full JSON-schema enforcement via
    ``responseSchema``/``responseMimeType``.
    """

    scheme = "gemini"

    def __init__(self, api_key: str, *, model: str = LLM_PRIMARY_MODEL, api_base: str = ""):
        self.api_key = api_key
        self.model = model
        self.api_base = (api_base or SCHEME_DEFAULT_BASE["gemini"]).rstrip("/")
        self.last_usage = dict(_ZERO_USAGE)

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        generation_config = {}
        if self.model.startswith("gemini-3"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "low"}
        else:
            generation_config["temperature"] = temperature
            if self.model.startswith("gemini-2.5"):
                generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if json_mode or response_schema is not None:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if response_schema is not None:
            payload["generationConfig"]["responseSchema"] = response_schema

        url = f"{self.api_base}/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        result = _post_json_with_retry(url, payload, headers, label=f"gemini ({self.model})")

        self.last_usage = _gemini_usage(result)
        candidate = result["candidates"][0]
        finish_reason = candidate.get("finishReason", "UNKNOWN")
        parts = candidate.get("content", {}).get("parts")
        if not parts:
            raise RuntimeError(f"Gemini returned no content (finishReason={finish_reason})")
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if text:
                return text
        raise RuntimeError(f"Gemini returned no text content (finishReason={finish_reason})")


class OpenAIProvider:
    """Scheme ``openai``: OpenAI-compatible ``/chat/completions``.

    Covers OpenAI plus any OpenAI-compatible endpoint (Groq, DeepSeek, xAI,
    OpenRouter, Together, Mistral, local, …) via ``api_base``. ``temperature`` is
    omitted by default (``send_temperature=False``) because ``gpt-5.4-mini`` and
    other reasoning models reject a non-default temperature. JSON via
    ``response_format={"type": "json_object"}`` — best-effort (no strict schema).
    """

    scheme = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = LLM_FALLBACK_MODEL,
        api_base: str = "",
        send_temperature: bool = False,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = (api_base or SCHEME_DEFAULT_BASE["openai"]).rstrip("/")
        self.send_temperature = send_temperature
        self.last_usage = dict(_ZERO_USAGE)

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.send_temperature:
            payload["temperature"] = temperature
        if json_mode or response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.api_base}/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        result = _post_json_with_retry(url, payload, headers, label=f"openai ({self.model})")

        self.last_usage = _openai_usage(result)
        choice = result["choices"][0]
        content = choice.get("message", {}).get("content")
        if not content:
            raise RuntimeError(
                f"OpenAI provider returned no content (finish_reason={choice.get('finish_reason')})"
            )
        return content


class AnthropicProvider:
    """Scheme ``anthropic``: the Messages API ``/messages``.

    Headers ``x-api-key`` + ``anthropic-version: 2023-06-01``; body carries the
    required ``max_tokens`` (``ANTHROPIC_MAX_TOKENS``). The Messages API has no
    ``json_object`` mode, so **JSON is prompt-driven only** — the eval/tailor
    prompts already instruct JSON output; ``json_mode``/``response_schema`` are
    accepted for interface parity but not sent on the wire. Offline-tested only
    until first live use.
    """

    scheme = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-haiku-4-5",
        api_base: str = "",
        max_tokens: int = ANTHROPIC_MAX_TOKENS,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = (api_base or SCHEME_DEFAULT_BASE["anthropic"]).rstrip("/")
        self.max_tokens = max_tokens
        self.last_usage = dict(_ZERO_USAGE)

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        url = f"{self.api_base}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        result = _post_json_with_retry(url, payload, headers, label=f"anthropic ({self.model})")

        self.last_usage = _anthropic_usage(result)
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return block["text"]
        raise RuntimeError(
            f"Anthropic returned no text content (stop_reason={result.get('stop_reason')})"
        )


# ── Scheme factory / registry ─────────────────────────────────────────────────
# Adding a future scheme = one Provider class + one entry here (the extension
# point); the provider resolves its own default base from SCHEME_DEFAULT_BASE.
_SCHEME_CLASSES = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


def build_provider(scheme: str, *, api_key: str, model: str, api_base: str = "", **opts):
    """Construct a provider from a scheme name, normalizing aliases/case.

    Unknown scheme → ``ValueError`` listing the supported schemes.
    """
    normalized = (scheme or "").strip().lower()
    canonical = _SCHEME_ALIASES.get(normalized, normalized)
    cls = _SCHEME_CLASSES.get(canonical)
    if cls is None:
        supported = ", ".join(sorted(_SCHEME_CLASSES))
        raise ValueError(f"Unknown LLM scheme {scheme!r}; supported schemes: {supported}")
    return cls(api_key, model=model, api_base=api_base, **opts)


# ── Role-generic client with a hardened breaker ───────────────────────────────
class LLMClient:
    """A primary provider with a circuit-breaker fallback to a second provider.

    The primary retries transient errors internally. A post-retry 429/503
    increments a consecutive-failure counter; the primary is disabled for the
    rest of the run only when the counter reaches the breaker threshold. A
    success resets the counter, so a single transient blip recovers. Other errors
    fall back per-request without disabling the primary. Thread-safe.
    """

    def __init__(
        self,
        primary,
        fallback=None,
        *,
        breaker_statuses=LLM_CIRCUIT_BREAK_STATUS,
        reject_statuses=LLM_MODEL_REJECT_STATUS,
        threshold: int = LLM_BREAKER_THRESHOLD,
    ):
        self.primary = primary
        self.fallback = fallback
        self._breaker_statuses = set(breaker_statuses)
        self._reject_statuses = set(reject_statuses)
        self._threshold = threshold
        self._lock = threading.Lock()
        self._primary_disabled = False
        self._primary_disabled_reason = ""
        self._consecutive_failures = 0
        self._primary_calls = 0   # successful primary responses
        self._fallback_calls = 0  # requests served by the fallback
        self._tokens = dict(_ZERO_USAGE)  # accumulated input/output/thinking/cached

    @classmethod
    def from_config(cls, cfg) -> "LLMClient":
        """Build both providers from a PipelineConfig-shaped object via the factory."""
        primary = build_provider(
            cfg.llm_primary_scheme,
            api_key=cfg.llm_primary_api_key,
            model=cfg.llm_primary_model,
            api_base=cfg.llm_primary_api_base,
        )
        fallback = None
        if cfg.llm_fallback_api_key:
            fallback = build_provider(
                cfg.llm_fallback_scheme,
                api_key=cfg.llm_fallback_api_key,
                model=cfg.llm_fallback_model,
                api_base=cfg.llm_fallback_api_base,
            )
        return cls(primary, fallback)

    @staticmethod
    def _label(provider) -> str:
        if provider is None:
            return "none"
        return f"{getattr(provider, 'scheme', '?')}/{getattr(provider, 'model', '?')}"

    @property
    def primary_label(self) -> str:
        return self._label(self.primary)

    @property
    def fallback_label(self) -> str:
        return self._label(self.fallback)

    def _accumulate(self, usage) -> None:
        """Add one call's token usage to the running totals (call under lock).

        Totals are best-effort: the per-provider ``last_usage`` is shared across
        worker threads, so under heavy concurrency a snapshot can occasionally be
        attributed to the wrong call. The totals never corrupt (the add is
        lock-guarded); this only affects the end-of-run telemetry line.
        """
        if not usage:
            return
        for key in self._tokens:
            self._tokens[key] += _int(usage.get(key))

    def token_usage(self) -> dict:
        with self._lock:
            return dict(self._tokens)

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        with self._lock:
            disabled = self._primary_disabled
        if disabled:
            return self._use_fallback(prompt, temperature, json_mode, response_schema)

        try:
            result = self.primary.generate(
                prompt, temperature=temperature, json_mode=json_mode, response_schema=response_schema
            )
        except urllib.error.HTTPError as exc:
            if exc.code in self._reject_statuses:
                # The provider will not serve this model/request — a retired or
                # misspelled model (404), or a rejected body (400). Retrying it
                # per job pays a doomed request every time, so say so once and
                # take the primary out for the rest of the run. Slightly
                # heavy-handed for a genuinely per-prompt 400, but the fallback
                # still serves every request and the log names the cause.
                self._record_primary_rejected(exc)
                return self._use_fallback(prompt, temperature, json_mode, response_schema)
            if exc.code in self._breaker_statuses:
                # Count toward the breaker; disable at threshold. Serve this
                # request via the fallback (or raise if there is none).
                self._record_primary_failure(exc.code)
                return self._use_fallback(prompt, temperature, json_mode, response_schema)
            # Other HTTP error: per-request fallback, primary stays enabled.
            if self.fallback is None:
                raise
            print(
                f"    Primary ({self.primary_label}) HTTP {exc.code} — falling back to "
                f"{self.fallback_label} for this request...",
                flush=True,
            )
            return self._use_fallback(prompt, temperature, json_mode, response_schema)
        except Exception as exc:
            # Non-HTTP error (timeout, connection reset, malformed body): per-request fallback.
            if self.fallback is None:
                raise
            print(
                f"    Primary ({self.primary_label}) error ({type(exc).__name__}) — falling back to "
                f"{self.fallback_label} for this request...",
                flush=True,
            )
            return self._use_fallback(prompt, temperature, json_mode, response_schema)

        with self._lock:
            self._primary_calls += 1
            self._consecutive_failures = 0  # a success clears the breaker streak
            self._accumulate(getattr(self.primary, "last_usage", None))
        return result

    def _use_fallback(self, prompt: str, temperature: float, json_mode: bool, response_schema=None) -> str:
        if self.fallback is None:
            raise RuntimeError(
                f"Primary ({self.primary_label}) unavailable "
                f"({self._primary_disabled_reason or 'error'}) and no fallback configured."
            )
        with self._lock:
            self._fallback_calls += 1
        result = self.fallback.generate(
            prompt, temperature=temperature, json_mode=json_mode, response_schema=response_schema
        )
        with self._lock:
            self._accumulate(getattr(self.fallback, "last_usage", None))
        return result

    @staticmethod
    def _error_detail(exc, limit: int = 200) -> str:
        """Best-effort one-line body of an HTTPError (providers name the cause there)."""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            return ""
        body = " ".join(body.split())
        return body[: limit - 1] + "…" if len(body) > limit else body

    def _record_primary_rejected(self, exc) -> None:
        """Disable the primary for the run after a model/request rejection, loudly once."""
        reason = f"HTTP {exc.code} (model or request rejected — check LLM_PRIMARY_MODEL)"
        with self._lock:
            if self._primary_disabled:
                return
            self._primary_disabled = True
            self._primary_disabled_reason = reason
        detail = self._error_detail(exc)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(
            f"    [LLM] {stamp} Primary model rejected — check LLM_PRIMARY_MODEL "
            f"({self.primary_label} answered HTTP {exc.code}"
            + (f": {detail}" if detail else "")
            + f"). Serving the rest of this run from {self.fallback_label}.",
            flush=True,
        )
        shutdown = model_shutdown_warning(getattr(self.primary, "model", ""))
        if shutdown:
            print(f"    {shutdown}", flush=True)

    def _record_primary_failure(self, code: int) -> None:
        label = {
            429: "429 RESOURCE_EXHAUSTED (rate limit — your quota)",
            503: "503 UNAVAILABLE (backend overloaded — provider side)",
        }.get(code, str(code))
        with self._lock:
            self._consecutive_failures += 1
            if self._primary_disabled or self._consecutive_failures < self._threshold:
                return
            self._primary_disabled = True
            self._primary_disabled_reason = label
            served = self._primary_calls
            failures = self._consecutive_failures
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(
            f"    [LLM] {stamp} Primary ({self.primary_label}) {label} — {failures} consecutive "
            f"failure(s) after {served} successful call(s) this run — disabling primary, switching "
            f"to {self.fallback_label} for the rest of the run.",
            flush=True,
        )

    def usage_summary(self) -> str:
        with self._lock:
            primary_calls, fallback_calls = self._primary_calls, self._fallback_calls
            reason = self._primary_disabled_reason
            tokens = dict(self._tokens)
        line = (
            f"[LLM] Usage this run — primary ({self.primary_label}): {primary_calls}, "
            f"fallback ({self.fallback_label}): {fallback_calls}."
        )
        if reason:
            line += f" Primary was disabled mid-run ({reason}) — consider adjusting limits."
        line += (
            f" Tokens — input: {tokens['input']}, output: {tokens['output']}, "
            f"thinking: {tokens['thinking']}, cached: {tokens['cached']}."
        )
        # Carried on the usage line so an approaching model retirement shows up
        # in the digest footer every run, not only in the CI log.
        shutdown = model_shutdown_warning(getattr(self.primary, "model", ""))
        if shutdown:
            line += " " + shutdown.replace("[LLM] ", "⚠️ ", 1)
        return line
