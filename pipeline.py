#!/usr/bin/env python3
"""
Job search pipeline: fetch → deduplicate → LLM evaluate → tailor resume → Telegram notify.

Required environment variables:
  GEMINI_API_KEY       — Gemini API key
  TELEGRAM_BOT_TOKEN   — Telegram bot token
  TELEGRAM_CHAT_ID     — Target chat/user ID

First run (no seen_jobs.json): jobs posted within 7 days are evaluated normally;
older jobs are silently marked seen.
"""

import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_JOBS_FILE = "seen_jobs.json"
CRITERIA_FILE = "criteria.md"
CV_TAILORING_PROMPT_FILE = "cv_tailoring_prompt.md"
BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Qwen fallback (Alibaba DashScope, OpenAI-compatible endpoint). Used when Gemini
# keeps failing with a transient error (e.g. 503 overloaded) after one retry.
QWEN_MODEL = "qwen-plus"
QWEN_API_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Status codes that trip the Gemini circuit-breaker: once Gemini returns one of
# these, we stop using Gemini for the rest of the run and switch to Qwen.
# 429 = rate limit (your quota); 503 = backend overloaded (Google's side).
GEMINI_CIRCUIT_BREAK_STATUS = {429, 503}

# Concurrency for the staged pipeline. Evaluation is Gemini-bound (paid tier, high
# limits) so it parallelizes freely; tailoring also compiles LaTeX, which is
# CPU-bound on the few-core CI runner, so keep that pool smaller. Both easy to tune.
EVAL_WORKERS = 8
TAILOR_WORKERS = 4

# ── Gemini client ────────────────────────────────────────────────────────────

class GeminiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "thinkingConfig": {"thinkingBudget": 0}},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent?key={self.api_key}"
        data = json.dumps(payload).encode()

        # Single attempt — no retry, no backoff sleep. Errors propagate to
        # LLMClient, whose circuit-breaker decides whether to disable Gemini
        # (on 429/503) and switch to Qwen.
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
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
        return parts[0]["text"]


class QwenClient:
    """Alibaba DashScope Qwen via the OpenAI-compatible chat/completions endpoint."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False) -> str:
        payload = {
            "model": QWEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{QWEN_API_BASE}/chat/completions"
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
                print(f"    Qwen transient error {exc.code} — waiting {delay}s (attempt {attempt}/{len(delays)})...", flush=True)
                time.sleep(delay)


class LLMClient:
    """Gemini as primary model, with a circuit-breaker fallback to Qwen.

    On the first Gemini 429 (rate limit) or 503 (backend overloaded), Gemini is
    disabled for the rest of the run and every subsequent request goes straight
    to Qwen — we don't keep hammering a limited/overloaded endpoint. Other Gemini
    errors (network, 500, etc.) fall back to Qwen per-request without disabling
    Gemini. Thread-safe: the eval/tailor stages call this from worker threads.
    """

    def __init__(self, gemini_api_key: str, qwen_api_key: str = ""):
        self.gemini = GeminiClient(gemini_api_key)
        self.qwen = QwenClient(qwen_api_key) if qwen_api_key else None
        self._lock = threading.Lock()
        self._gemini_disabled = False
        self._gemini_disabled_reason = ""
        self._gemini_calls = 0   # successful Gemini responses
        self._qwen_calls = 0     # requests served by Qwen

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False) -> str:
        with self._lock:
            disabled = self._gemini_disabled
        if disabled:
            return self._use_qwen(prompt, temperature, json_mode)

        try:
            result = self.gemini.generate(prompt, temperature=temperature, json_mode=json_mode)
            with self._lock:
                self._gemini_calls += 1
            return result
        except urllib.error.HTTPError as exc:
            if exc.code in GEMINI_CIRCUIT_BREAK_STATUS:
                self._disable_gemini(exc.code)
                return self._use_qwen(prompt, temperature, json_mode)
            # Other HTTP errors: per-request fallback, Gemini stays enabled.
            if self.qwen is None:
                raise
            print(f"    Gemini HTTP {exc.code} — falling back to Qwen for this request...", flush=True)
            return self._use_qwen(prompt, temperature, json_mode)
        except Exception as exc:
            # Non-HTTP error (timeout, connection reset, malformed body): per-request fallback.
            if self.qwen is None:
                raise
            print(f"    Gemini error ({type(exc).__name__}) — falling back to Qwen for this request...", flush=True)
            return self._use_qwen(prompt, temperature, json_mode)

    def _use_qwen(self, prompt: str, temperature: float, json_mode: bool) -> str:
        if self.qwen is None:
            raise RuntimeError(
                f"Gemini unavailable ({self._gemini_disabled_reason or 'error'}) and no Qwen fallback configured."
            )
        with self._lock:
            self._qwen_calls += 1
        return self.qwen.generate(prompt, temperature=temperature, json_mode=json_mode)

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
            f"    [LLM] {stamp} Gemini {label} after {served} successful call(s) this run "
            f"— disabling Gemini, switching to Qwen ({QWEN_MODEL}) for the rest of the run.",
            flush=True,
        )

    def usage_summary(self) -> str:
        with self._lock:
            gemini, qwen = self._gemini_calls, self._qwen_calls
            reason = self._gemini_disabled_reason
        line = f"[LLM] Usage this run — Gemini: {gemini}, Qwen: {qwen}."
        if reason:
            line += f" Gemini was disabled mid-run ({reason}) — consider adjusting limits."
        return line


# ── State persistence ────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


def title_company_key(title: str, company: str, location: str = "") -> str:
    key = "{}|{}".format(title.lower().strip(), company.lower().strip())
    norm_location = " ".join(location.lower().split())
    if norm_location:
        key = "{}|{}".format(key, norm_location)
    return key


def load_seen_jobs():
    """Returns a set of seen URL keys, or None if the state file doesn't exist (first run)."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return None
    with open(SEEN_JOBS_FILE) as f:
        return set(json.load(f))


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


# ── Prompt loading ───────────────────────────────────────────────────────────

def load_criteria() -> str:
    with open(CRITERIA_FILE) as f:
        return f.read()


def load_tailoring_instructions() -> str:
    """Extract STEP 3 through (not including) BASE LaTeX TEMPLATE."""
    with open(CV_TAILORING_PROMPT_FILE) as f:
        content = f.read()
    start = content.index("## STEP 3")
    end = content.index("## BASE LaTeX TEMPLATE")
    return content[start:end].strip()


def load_base_tex() -> str:
    with open(BASE_TEX_FILE) as f:
        return f.read()


# ── LLM tasks ────────────────────────────────────────────────────────────────

def evaluate_job(client: GeminiClient, criteria: str, job: dict) -> dict:
    """Returns {"fit": bool, "reason": str, "timezone_note": str|None}."""
    prompt = f"""You are evaluating a job posting for Igor Pivnyk, an iOS/macOS developer based in Israel (UTC+3).

## Fit Criteria

{criteria}

## Job Posting

Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Remote: {job.get("is_remote", "")}
Source: {job.get("source", "")}
URL: {job.get("url", "")}

Description:
{job.get("description", "")[:5000]}

## Your Task

Decide whether this job fits Igor's criteria. Return a JSON object with exactly these fields:
- "fit": true or false
- "reason": one or two sentences explaining the decision
- "timezone_note": a warning string if the role requires strict US business hours only, otherwise null
"""
    raw = client.generate(prompt, temperature=0.0, json_mode=True)
    result = json.loads(raw)
    if isinstance(result.get("fit"), str):
        result["fit"] = result["fit"].lower() == "true"
    return result


def tailor_resume(client: GeminiClient, tailoring_instructions: str, base_tex: str, job: dict) -> str:
    """Returns tailored LaTeX source (code fences stripped)."""
    job_text = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"URL: {job.get('url', '')}\n\n"
        f"{job.get('description', '')[:7000]}"
    )
    prompt = f"""You are a professional resume writer. Tailor Igor Pivnyk's CV for the job posting below.

{tailoring_instructions}

## Produce the LaTeX file

Write the complete, compilable LaTeX source. Start from the base template and apply your changes. \
Output the entire .tex file — do not truncate. Output raw LaTeX only — no markdown fences, \
no explanation before or after.

## Base LaTeX Template

{base_tex}

## Job Posting

{job_text}
"""
    raw = client.generate(prompt, temperature=0.2)
    return _strip_latex_fences(raw)


def _strip_latex_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:latex)?\n(.*)\n```$", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Extract from \documentclass to \end{document} to drop any surrounding prose
    m = re.search(r'(\\documentclass.*?\\end\{document\})', text, re.DOTALL)
    if m:
        return m.group(1)
    return text


# ── LaTeX compilation ────────────────────────────────────────────────────────

def _extract_latex_errors(log: str) -> str:
    """Pull error blocks (lines starting with '!') from an xelatex log."""
    lines = log.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("!"):
            block = lines[i : i + 10]
            blocks.append("\n".join(block))
            i += 10
        else:
            i += 1
    if blocks:
        return "\n\n".join(blocks[:5])
    # Fallback: last 40 lines usually contain the relevant failure context.
    return "\n".join(lines[-40:])


def _compile_latex(tex_source: str) -> tuple:
    """
    Compile tex_source with xelatex.
    Returns (success: bool, pdf_bytes: bytes|None, error_excerpt: str).
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "cv.tex")
            pdf_path = os.path.join(tmpdir, "cv.pdf")
            log_path = os.path.join(tmpdir, "cv.log")

            # Inject the real phone number (kept out of the repo and out of the
            # LLM prompt) only at compile time. When CV_PHONE is unset the token
            # collapses to nothing, leaving no dangling separator.
            phone = os.environ.get("CV_PHONE", "").strip()
            phone_tex = f"\\enspace\\textbar\\enspace {phone}" if phone else ""
            tex_source = tex_source.replace("((PHONE))", phone_tex)

            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(tex_source)

            cmd = ["xelatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path]
            # Run twice so cross-references resolve.
            for _ in range(2):
                subprocess.run(cmd, capture_output=True, timeout=120)

            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return True, f.read(), ""

            error_excerpt = ""
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    error_excerpt = _extract_latex_errors(f.read())
            return False, None, error_excerpt

    except FileNotFoundError:
        return False, None, "xelatex not found — cannot compile PDF"


def _fix_latex(client: "GeminiClient", tex_source: str, error_excerpt: str) -> str:
    """Ask Gemini to fix a broken LaTeX source given the compiler error."""
    prompt = f"""The LaTeX source below failed to compile with xelatex. Fix the compilation errors.

## Compiler errors

{error_excerpt}

## Broken LaTeX source

{tex_source}

Fix only what is broken. Do not change the content or layout. \
Output the complete fixed .tex file — raw LaTeX only, no markdown fences, no explanation.
"""
    raw = client.generate(prompt, temperature=0.0)
    return _strip_latex_fences(raw)


def compile_with_fixes(client: "GeminiClient", tex_source: str, max_attempts: int = 3) -> tuple:
    """
    Try to compile tex_source, asking Gemini to fix errors between attempts.
    Returns (success: bool, pdf_bytes: bytes|None, final_tex: str).
    """
    for attempt in range(1, max_attempts + 1):
        success, pdf_bytes, error_excerpt = _compile_latex(tex_source)
        if success:
            return True, pdf_bytes, tex_source
        print(f"    Compilation failed (attempt {attempt}/{max_attempts}): {error_excerpt[:120]}", flush=True)
        if attempt < max_attempts:
            print(f"    Asking Gemini to fix LaTeX errors...", flush=True)
            try:
                tex_source = _fix_latex(client, tex_source, error_excerpt)
            except Exception as exc:
                print(f"    Fix request failed: {exc}", file=sys.stderr)
                break
    return False, None, tex_source


# ── Telegram ─────────────────────────────────────────────────────────────────

def _tg_send_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _tg_send_document(bot_token: str, chat_id: str, filename: str, content: bytes, caption: str) -> None:
    boundary = "PipelineBoundary8a3f1d6e"
    crlf = b"\r\n"

    def part_field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        ).encode()

    body = (
        part_field("chat_id", chat_id)
        + part_field("caption", caption)
        + (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n"
            f"\r\n"
        ).encode()
        + content
        + crlf
        + f"--{boundary}--\r\n".encode()
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _company_slug(company: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_")
    return slug or "unknown"


def fetch_job_text_from_url(url: str) -> str:
    """Best-effort fetch of a job posting's text from a URL.

    Returns plain text (HTML stripped, whitespace collapsed), or "" on any
    failure (HTTP error, timeout, empty body). The caller decides whether the
    result is usable or whether the user must fall back to pasting the text;
    many job boards block CI IPs or require JS, so an empty return is expected.
    """
    from portable_job_scraper import strip_html

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
    except Exception as exc:
        print(f"    URL fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ""

    # Drop the contents of script/style/head/noscript blocks first — strip_html
    # only removes tags, not the CSS/JS text between them, which would otherwise
    # flood the LLM prompt with markup noise.
    body = re.sub(r"(?is)<(script|style|head|noscript)\b.*?</\1>", " ", body)
    text = strip_html(body)
    return " ".join(text.split())


def _format_notification(job: dict, evaluation: dict) -> str:
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("location", "")
    url = job.get("url", "")
    source = job.get("source", "")
    reason = evaluation.get("reason", "")
    timezone_note = evaluation.get("timezone_note")

    lines = [
        f"<b>{title}</b>",
        f"<b>{company}</b>" + (f" — {location}" if location else ""),
        f'<a href="{url}">View posting</a>  |  Source: {source}',
        "",
        f"<i>{reason}</i>",
    ]
    if timezone_note:
        lines.append(f"\n⚠️ <b>Timezone:</b> {timezone_note}")

    return "\n".join(lines)


# ── Job processing ───────────────────────────────────────────────────────────

def prepare_fit(gemini: GeminiClient, tailoring_instructions: str, base_tex: str, job: dict, evaluation: dict) -> dict:
    """
    Tailor + compile the CV for a job already judged a fit, and assemble the
    Telegram send-payload. Performs NO Telegram I/O — pure preparation, so it is
    safe to run concurrently across jobs. Tailoring/compilation failures are soft:
    we fall back to raw .tex or no document.

    Returns a payload dict consumed by send_fit().
    """
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"    Fit! Tailoring resume: {title} at {company}", flush=True)

    tex_source = None
    try:
        tex_source = tailor_resume(gemini, tailoring_instructions, base_tex, job)
    except Exception as exc:
        print(f"    Tailoring error: {exc}", file=sys.stderr)

    pdf_bytes = None
    final_tex = tex_source
    compilation_failed = False
    if tex_source:
        print(f"    Compiling PDF: {title} at {company}...", flush=True)
        ok, pdf_bytes, final_tex = compile_with_fixes(gemini, tex_source)
        if ok:
            print(f"    PDF compiled successfully: {title} at {company}.", flush=True)
        else:
            compilation_failed = True
            print(f"    PDF compilation failed after all attempts ({title} at {company}) — will send .tex as fallback.", flush=True)

    message = _format_notification(job, evaluation)
    if compilation_failed:
        message += "\n\n⚠️ <b>Note:</b> PDF compilation failed — raw LaTeX attached instead."

    return {
        "title": title,
        "company": company,
        "message": message,
        "pdf_bytes": pdf_bytes,
        "final_tex": final_tex,
    }


def send_fit(payload: dict) -> None:
    """
    Send a prepared fit to Telegram: the notification message, then the tailored
    CV document (PDF, or .tex fallback). Raises if the message send fails so the
    caller can leave the job unseen for a retry next run; document-send failures
    are soft (logged, not raised).
    """
    title = payload["title"]
    company = payload["company"]

    # Let Telegram message errors propagate — caller will not mark the job as seen.
    _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, payload["message"])

    slug = _company_slug(company)
    pdf_bytes = payload.get("pdf_bytes")
    final_tex = payload.get("final_tex")
    if pdf_bytes:
        try:
            _tg_send_document(
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                f"igor_pivnyk_cv_{slug}.pdf", pdf_bytes,
                caption=f"Tailored CV — {title} at {company}",
            )
        except Exception as exc:
            print(f"    Telegram PDF error: {exc}", file=sys.stderr)
    elif final_tex:
        try:
            _tg_send_document(
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                f"igor_pivnyk_cv_{slug}.tex", final_tex.encode("utf-8"),
                caption=f"Tailored CV (LaTeX source) — {title} at {company}",
            )
        except Exception as exc:
            print(f"    Telegram document error: {exc}", file=sys.stderr)


def process_job(gemini: GeminiClient, criteria: str, tailoring_instructions: str, base_tex: str, job: dict) -> bool:
    """
    Evaluate → tailor → compile → send a single job end-to-end.
    Returns True if a notification was sent (job was a fit).
    Raises on errors that warrant a retry next run (evaluation failure, Telegram send failure).
    Tailoring/compilation failures are soft — we fall back to no CV or raw .tex.

    Thin wrapper over evaluate_job/prepare_fit/send_fit, kept for --test mode.
    """
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"  Evaluating: {title} at {company}", flush=True)

    # Let evaluation errors propagate — caller will not mark the job as seen.
    evaluation = evaluate_job(gemini, criteria, job)

    if not evaluation.get("fit"):
        print(f"    Skip — {evaluation.get('reason', '')}")
        return False

    payload = prepare_fit(gemini, tailoring_instructions, base_tex, job, evaluation)
    send_fit(payload)
    return True

    return True


# ── On-demand single-job tailoring ───────────────────────────────────────────

# Minimum length the job description must reach before we trust it enough to
# tailor against. Below this we assume the URL fetch failed (blocked/JS page)
# and ask the user to paste the text instead, rather than emit a weak CV.
MIN_JOB_TEXT_LEN = 200


def tailor_single_job(client: "LLMClient", job: dict) -> None:
    """Tailor + compile + Telegram-deliver a CV for one manually supplied job.

    Reuses the same tailoring/compilation/delivery path as the scheduled
    pipeline (see process_job). Does NOT touch seen_jobs.json — this is an
    on-demand action, not part of dedup state.
    """
    title = job.get("title", "iOS Developer")
    company = job.get("company", "the role")

    print(f"  Tailoring CV for: {title} at {company}", flush=True)
    tex_source = tailor_resume(client, load_tailoring_instructions(), load_base_tex(), job)

    print("  Compiling PDF...", flush=True)
    ok, pdf_bytes, final_tex = compile_with_fixes(client, tex_source)

    header = (
        f"<b>{title}</b>\n"
        f"<b>{company}</b>" + (f" — {job['location']}" if job.get("location") else "") + "\n"
        + (f'<a href="{job["url"]}">View posting</a>\n' if job.get("url") else "")
        + "\n📄 Tailored CV attached."
    )
    if not ok:
        header += "\n\n⚠️ <b>Note:</b> PDF compilation failed — raw LaTeX attached instead."
    _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, header)

    slug = _company_slug(company)
    if pdf_bytes:
        _tg_send_document(
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
            f"igor_pivnyk_cv_{slug}.pdf", pdf_bytes,
            caption=f"Tailored CV — {title} at {company}",
        )
        print("  PDF sent to Telegram.", flush=True)
    else:
        _tg_send_document(
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
            f"igor_pivnyk_cv_{slug}.tex", final_tex.encode("utf-8"),
            caption=f"Tailored CV (LaTeX source) — {title} at {company}",
        )
        print("  LaTeX source sent to Telegram (compilation failed).", flush=True)


def run_tailor(args) -> None:
    """Entry point for `pipeline.py --tailor`: build one job dict, then tailor it."""
    if not all([GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("Error: GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        sys.exit(1)

    description = (args.job_text or "").strip()
    if not description and args.url:
        print(f"  Fetching job text from {args.url}", flush=True)
        description = fetch_job_text_from_url(args.url)

    if len(description) < MIN_JOB_TEXT_LEN:
        print(
            "Error: could not obtain enough job-description text "
            f"(got {len(description)} chars, need >= {MIN_JOB_TEXT_LEN}).\n"
            "The URL was likely blocked or requires JavaScript. Re-run and pass "
            "the description directly via --job-text (paste fallback).",
            file=sys.stderr,
        )
        sys.exit(1)

    company = (args.company or "").strip()
    if not company and args.url:
        host = urllib.parse.urlparse(args.url).netloc
        company = host.replace("www.", "").split(".")[0] if host else ""

    job = {
        "title": (args.title or "iOS Developer").strip(),
        "company": company or "the role",
        "location": (args.location or "").strip(),
        "url": (args.url or "").strip(),
        "description": description,
    }

    try:
        client = LLMClient(GEMINI_API_KEY, QWEN_API_KEY)
        tailor_single_job(client, job)
        print("Done.", flush=True)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc)
        raise


# ── Main ─────────────────────────────────────────────────────────────────────

def _send_error_notification(exc: Exception) -> None:
    try:
        text = f"⚠️ <b>Pipeline error</b>\n\n<code>{type(exc).__name__}: {exc}</code>"
        _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, text)
    except Exception:
        pass


def main():
    import argparse
    from portable_job_scraper import fetch_jobs, job_to_dict

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test",
        action="store_true",
        help="Force-process one job through the full pipeline to verify everything works. Does not modify seen_jobs.json.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Fetch and print new jobs (not in seen_jobs.json) without AI evaluation or Telegram. Does not modify seen_jobs.json.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Mark all currently fetched jobs as seen without evaluating. Resets the baseline.",
    )
    parser.add_argument(
        "--tailor",
        action="store_true",
        help="Tailor a CV for ONE manually supplied job and send it to Telegram. "
             "Provide --url (auto-fetched) and/or --job-text (paste fallback). "
             "Does not touch seen_jobs.json.",
    )
    parser.add_argument("--url", default="", help="Job posting URL (used with --tailor).")
    parser.add_argument("--job-text", dest="job_text", default="",
                        help="Job description text, pasted directly (used with --tailor).")
    parser.add_argument("--title", default="", help="Job title (used with --tailor).")
    parser.add_argument("--company", default="", help="Company name (used with --tailor).")
    parser.add_argument("--location", default="", help="Job location (used with --tailor).")
    args = parser.parse_args()

    if args.tailor:
        run_tailor(args)
        return

    if args.seed:
        raw_jobs = fetch_jobs(verbose=True)
        seen_raw = load_seen_jobs()
        seen = seen_raw if seen_raw is not None else set()
        added = 0
        for j in raw_jobs:
            key = normalize_url(j.url)
            tc_key = title_company_key(j.title, j.company, j.location)
            if key not in seen:
                seen.add(key)
                added += 1
            if tc_key not in seen:
                seen.add(tc_key)
        save_seen_jobs(seen)
        print(f"Seed complete — {len(raw_jobs)} job(s) marked seen ({added} new URL entries added).")
        return

    if args.list:
        raw_jobs = fetch_jobs(verbose=True)
        seen_raw = load_seen_jobs()
        seen = seen_raw if seen_raw is not None else set()
        new_jobs = [j for j in raw_jobs if normalize_url(j.url) not in seen and title_company_key(j.title, j.company, j.location) not in seen]
        print(f"{len(new_jobs)} new job(s):\n")
        for j in new_jobs:
            date_str = j.date_posted.strftime("%Y-%m-%d") if j.date_posted else "n/a"
            print(f"  {j.title}")
            print(f"  {j.company} | {j.location or 'n/a'} | {j.source} | {date_str}")
            print(f"  {j.url}")
            print()
        return

    if not all([GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("Error: GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        sys.exit(1)

    try:
        gemini = LLMClient(GEMINI_API_KEY, QWEN_API_KEY)
        if not QWEN_API_KEY:
            print("Note: QWEN_API_KEY not set — no fallback model available.", flush=True)
        criteria = load_criteria()
        tailoring_instructions = load_tailoring_instructions()
        base_tex = load_base_tex()

        print("Fetching jobs...", flush=True)
        raw_jobs = fetch_jobs(verbose=True)

        if args.test:
            if not raw_jobs:
                print("No jobs found — nothing to test.")
                return
            j = raw_jobs[0]
            d = job_to_dict(j)
            d["description"] = j.description
            print("Test mode: processing one job without touching seen_jobs.json.")
            process_job(gemini, criteria, tailoring_instructions, base_tex, d)
            print("Done.", flush=True)
            return

        seen_raw = load_seen_jobs()
        first_run = seen_raw is None
        seen = seen_raw if seen_raw is not None else set()

        cutoff = datetime.date.today() - datetime.timedelta(days=7)

        # new_jobs: list of (url_key, tc_key, job_dict)
        # Keys are NOT added to seen yet — added only after successful processing.
        new_jobs = []
        for j in raw_jobs:
            key = normalize_url(j.url)
            tc_key = title_company_key(j.title, j.company, j.location)
            if key in seen or tc_key in seen:
                continue
            if first_run:
                # On first run, silently mark jobs older than 7 days as seen without evaluating.
                dp = j.date_posted
                posted = dp.date() if isinstance(dp, datetime.datetime) else dp  # may be date or None
                if posted is not None and posted < cutoff:
                    seen.add(key)
                    seen.add(tc_key)
                    continue
            d = job_to_dict(j)
            d["description"] = j.description
            new_jobs.append((key, tc_key, d))

        # Persist seen set now — captures first-run silenced jobs; new jobs are NOT yet included.
        save_seen_jobs(seen)
        print(f"Found {len(new_jobs)} new job(s).", flush=True)

        # ── Stage 2: Evaluate all new jobs concurrently ──────────────────────
        # Gemini calls are independent and the client is stateless, so we fan out
        # across a thread pool. seen-set mutation stays on this (main) thread as
        # results arrive — no locks needed.
        fits = []  # list of (key, tc_key, job, evaluation) for jobs judged a fit
        if new_jobs:
            print(f"Evaluating {len(new_jobs)} job(s) with {EVAL_WORKERS} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=EVAL_WORKERS) as pool:
                future_to_job = {
                    pool.submit(evaluate_job, gemini, criteria, job): (key, tc_key, job)
                    for key, tc_key, job in new_jobs
                }
                for future in concurrent.futures.as_completed(future_to_job):
                    key, tc_key, job = future_to_job[future]
                    try:
                        evaluation = future.result()
                    except Exception as exc:
                        # Evaluation failed — leave unseen so it retries next run.
                        print(
                            f"  Error evaluating '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    if evaluation.get("fit"):
                        fits.append((key, tc_key, job, evaluation))
                    else:
                        # Not a fit: mark seen so it won't be reprocessed.
                        print(f"    Skip '{job.get('title')}' — {evaluation.get('reason', '')}")
                        seen.add(key)
                        seen.add(tc_key)
        # Persist the non-fits captured above in one write.
        save_seen_jobs(seen)
        print(f"{len(fits)} fit(s) to tailor.", flush=True)

        # ── Stage 3: Tailor + compile the fits concurrently ──────────────────
        # Tailoring is Gemini-bound and compilation is CPU-bound; a smaller pool
        # keeps parallel xelatex runs from starving the runner. No Telegram I/O
        # happens here, so order doesn't matter and failures stay soft.
        prepared = []  # list of (key, tc_key, payload) ready to send
        if fits:
            print(f"Tailoring {len(fits)} CV(s) with {TAILOR_WORKERS} workers...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=TAILOR_WORKERS) as pool:
                future_to_fit = {
                    pool.submit(prepare_fit, gemini, tailoring_instructions, base_tex, job, evaluation): (key, tc_key, job)
                    for key, tc_key, job, evaluation in fits
                }
                for future in concurrent.futures.as_completed(future_to_fit):
                    key, tc_key, job = future_to_fit[future]
                    try:
                        payload = future.result()
                    except Exception as exc:
                        # prepare_fit swallows tailoring/compile errors internally,
                        # so this is unexpected — leave unseen to retry next run.
                        print(
                            f"  Error preparing '{job.get('title')}' — will retry next run: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    prepared.append((key, tc_key, payload))

        # ── Stage 4: Send to Telegram sequentially ───────────────────────────
        # Sequential to preserve message order and stay polite to the Telegram API.
        # A successful send marks the job seen; a failed send leaves it for retry.
        sent = 0
        for key, tc_key, payload in prepared:
            try:
                send_fit(payload)
                sent += 1
                seen.add(key)
                seen.add(tc_key)
                save_seen_jobs(seen)
            except Exception as exc:
                print(
                    f"  Error sending '{payload.get('title')}' — will retry next run: {exc}",
                    file=sys.stderr,
                )

        if sent == 0:
            noun = "new posting" if len(new_jobs) == 1 else "new postings"
            msg = (
                f"✅ Job search complete — {len(new_jobs)} {noun} checked, "
                f"none matched your criteria."
            )
            try:
                _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
            except Exception as exc:
                print(f"Telegram notification error: {exc}", file=sys.stderr)

        print(gemini.usage_summary(), flush=True)
        print("Done.", flush=True)

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc)
        raise


if __name__ == "__main__":
    main()
