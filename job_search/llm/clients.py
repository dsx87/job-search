"""Gemini primary client with a circuit-breaker fallback to Qwen.

Thread-safe: the eval/tailor stages call generate() from worker threads, and the
circuit-breaker state is guarded by self._lock.
"""
import datetime
import json
import threading
import time
import urllib.error
import urllib.request

from ..config import (
    GEMINI_API_BASE,
    GEMINI_CIRCUIT_BREAK_STATUS,
    GEMINI_MODEL,
    QWEN_API_BASE,
    QWEN_MODEL,
    RETRYABLE_STATUS,
)


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = GEMINI_MODEL,
        api_base: str = GEMINI_API_BASE,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")

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
        data = json.dumps(payload).encode()

        # Single attempt — no retry, no backoff sleep. Errors propagate to
        # LLMClient, whose circuit-breaker decides whether to disable Gemini
        # (on 429/503) and switch to Qwen.
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        candidate = result["candidates"][0]
        finish_reason = candidate.get("finishReason", "UNKNOWN")
        parts = candidate.get("content", {}).get("parts")
        if not parts:
            raise RuntimeError(
                f"Gemini returned no content (finishReason={finish_reason})"
            )
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if text:
                return text
        raise RuntimeError(
            f"Gemini returned no text content (finishReason={finish_reason})"
        )


class QwenClient:
    """Alibaba DashScope Qwen via the OpenAI-compatible chat/completions endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = QWEN_MODEL,
        api_base: str = QWEN_API_BASE,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode or response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.api_base}/chat/completions"
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        delays = [30, 60, 120]
        for attempt, delay in enumerate(delays, 1):
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read())
                choice = result["choices"][0]
                content = choice.get("message", {}).get("content")
                if not content:
                    raise RuntimeError(
                        f"Qwen returned no content (finishReason={choice.get('finish_reason')})"
                    )
                return content
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS or attempt == len(delays):
                    raise
                print(f"    Qwen ({self.model}) transient error {exc.code} — waiting {delay}s (attempt {attempt}/{len(delays)})...", flush=True)
                time.sleep(delay)


class LLMClient:
    """Gemini as primary model, with a circuit-breaker fallback to Qwen.

    On the first Gemini 429 (rate limit) or 503 (backend overloaded), Gemini is
    disabled for the rest of the run and every subsequent request goes straight
    to Qwen — we don't keep hammering a limited/overloaded endpoint. Other Gemini
    errors (network, 500, etc.) fall back to Qwen per-request without disabling
    Gemini. Thread-safe: the eval/tailor stages call this from worker threads.
    """

    def __init__(
        self,
        gemini_api_key: str,
        qwen_api_key: str = "",
        *,
        gemini_model: str = GEMINI_MODEL,
        gemini_api_base: str = GEMINI_API_BASE,
        qwen_model: str = QWEN_MODEL,
        qwen_api_base: str = QWEN_API_BASE,
    ):
        self.gemini_model = gemini_model
        self.qwen_model = qwen_model
        self.gemini = GeminiClient(
            gemini_api_key,
            model=gemini_model,
            api_base=gemini_api_base,
        )
        self.qwen = (
            QwenClient(qwen_api_key, model=qwen_model, api_base=qwen_api_base)
            if qwen_api_key
            else None
        )
        self._lock = threading.Lock()
        self._gemini_disabled = False
        self._gemini_disabled_reason = ""
        self._gemini_calls = 0   # successful Gemini responses
        self._qwen_calls = 0     # requests served by Qwen

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False, response_schema=None) -> str:
        with self._lock:
            disabled = self._gemini_disabled
        if disabled:
            return self._use_qwen(prompt, temperature, json_mode, response_schema)

        try:
            result = self.gemini.generate(prompt, temperature=temperature, json_mode=json_mode, response_schema=response_schema)
            with self._lock:
                self._gemini_calls += 1
            return result
        except urllib.error.HTTPError as exc:
            if exc.code in GEMINI_CIRCUIT_BREAK_STATUS:
                self._disable_gemini(exc.code)
                return self._use_qwen(prompt, temperature, json_mode, response_schema)
            # Other HTTP errors: per-request fallback, Gemini stays enabled.
            if self.qwen is None:
                raise
            print(f"    Gemini ({self.gemini_model}) HTTP {exc.code} — falling back to Qwen ({self.qwen_model}) for this request...", flush=True)
            return self._use_qwen(prompt, temperature, json_mode, response_schema)
        except Exception as exc:
            # Non-HTTP error (timeout, connection reset, malformed body): per-request fallback.
            if self.qwen is None:
                raise
            print(f"    Gemini ({self.gemini_model}) error ({type(exc).__name__}) — falling back to Qwen ({self.qwen_model}) for this request...", flush=True)
            return self._use_qwen(prompt, temperature, json_mode, response_schema)

    def _use_qwen(self, prompt: str, temperature: float, json_mode: bool, response_schema=None) -> str:
        if self.qwen is None:
            raise RuntimeError(
                f"Gemini ({self.gemini_model}) unavailable ({self._gemini_disabled_reason or 'error'}) and no Qwen ({self.qwen_model}) fallback configured."
            )
        with self._lock:
            self._qwen_calls += 1
        return self.qwen.generate(prompt, temperature=temperature, json_mode=json_mode, response_schema=response_schema)

    def _disable_gemini(self, code: int) -> None:
        label = {
            429: "429 RESOURCE_EXHAUSTED (rate limit — your quota)",
            503: "503 UNAVAILABLE (backend overloaded — Google's side)",
        }.get(code, str(code))
        with self._lock:
            # Only the first thread to trip the breaker logs it.
            if self._gemini_disabled:
                return
            self._gemini_disabled = True
            self._gemini_disabled_reason = label
            served = self._gemini_calls
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(
            f"    [LLM] {stamp} Gemini ({self.gemini_model}) {label} after {served} successful call(s) this run "
            f"— disabling Gemini, switching to Qwen ({self.qwen_model}) for the rest of the run.",
            flush=True,
        )

    def usage_summary(self) -> str:
        with self._lock:
            gemini, qwen = self._gemini_calls, self._qwen_calls
            reason = self._gemini_disabled_reason
        line = (
            f"[LLM] Usage this run — Gemini ({self.gemini_model}): {gemini}, "
            f"Qwen ({self.qwen_model}): {qwen}."
        )
        if reason:
            line += f" Gemini was disabled mid-run ({reason}) — consider adjusting limits."
        return line
