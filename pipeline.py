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

import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_JOBS_FILE = "seen_jobs.json"
CRITERIA_FILE = "criteria.md"
CV_TAILORING_PROMPT_FILE = "cv_tailoring_prompt.md"
BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ── Gemini client ────────────────────────────────────────────────────────────

class GeminiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, prompt: str, temperature: float = 0.0, json_mode: bool = False) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent?key={self.api_key}"
        data = json.dumps(payload).encode()

        delays = [30, 60, 120]
        for attempt, delay in enumerate(delays, 1):
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            try:
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
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == len(delays):
                    raise
                print(f"    Gemini rate limit — waiting {delay}s (attempt {attempt}/{len(delays)})...", flush=True)
                time.sleep(delay)


# ── State persistence ────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


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

def process_job(gemini: GeminiClient, criteria: str, tailoring_instructions: str, base_tex: str, job: dict) -> bool:
    """
    Returns True if a notification was sent (job was a fit).
    Raises on errors that warrant a retry next run (evaluation failure, Telegram send failure).
    Tailoring/compilation failures are soft — we fall back to no CV or raw .tex.
    """
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"  Evaluating: {title} at {company}", flush=True)

    # Let evaluation errors propagate — caller will not mark the job as seen.
    evaluation = evaluate_job(gemini, criteria, job)

    if not evaluation.get("fit"):
        print(f"    Skip — {evaluation.get('reason', '')}")
        return False

    print(f"    Fit! Tailoring resume...", flush=True)

    tex_source = None
    try:
        tex_source = tailor_resume(gemini, tailoring_instructions, base_tex, job)
    except Exception as exc:
        print(f"    Tailoring error: {exc}", file=sys.stderr)

    pdf_bytes = None
    final_tex = tex_source
    compilation_failed = False
    if tex_source:
        print(f"    Compiling PDF...", flush=True)
        ok, pdf_bytes, final_tex = compile_with_fixes(gemini, tex_source)
        if ok:
            print(f"    PDF compiled successfully.", flush=True)
        else:
            compilation_failed = True
            print(f"    PDF compilation failed after all attempts — will send .tex as fallback.", flush=True)

    message = _format_notification(job, evaluation)
    if compilation_failed:
        message += "\n\n⚠️ <b>Note:</b> PDF compilation failed — raw LaTeX attached instead."

    # Let Telegram message errors propagate — caller will not mark the job as seen.
    _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)

    slug = _company_slug(company)
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

    return True


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
    args = parser.parse_args()

    try:
        gemini = GeminiClient(GEMINI_API_KEY)
        criteria = load_criteria()
        tailoring_instructions = load_tailoring_instructions()
        base_tex = load_base_tex()

        print("Fetching jobs...", flush=True)
        raw_jobs = fetch_jobs()

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

        # new_jobs: list of (normalized_url_key, job_dict)
        # Keys are NOT added to seen yet — added only after successful processing.
        new_jobs = []
        for j in raw_jobs:
            key = normalize_url(j.url)
            if key in seen:
                continue
            if first_run:
                # On first run, silently mark jobs older than 7 days as seen without evaluating.
                dp = j.date_posted
                posted = dp.date() if isinstance(dp, datetime.datetime) else dp  # may be date or None
                if posted is not None and posted < cutoff:
                    seen.add(key)
                    continue
            d = job_to_dict(j)
            d["description"] = j.description
            new_jobs.append((key, d))

        # Persist seen set now — captures first-run silenced jobs; new jobs are NOT yet included.
        save_seen_jobs(seen)
        print(f"Found {len(new_jobs)} new job(s).", flush=True)

        fits = 0
        for key, job in new_jobs:
            try:
                if process_job(gemini, criteria, tailoring_instructions, base_tex, job):
                    fits += 1
                # Success (fit or not-fit): mark seen so it won't be reprocessed.
                seen.add(key)
                save_seen_jobs(seen)
            except Exception as exc:
                print(
                    f"  Error processing '{job.get('title')}' — will retry next run: {exc}",
                    file=sys.stderr,
                )
            time.sleep(1)

        if fits == 0:
            noun = "new posting" if len(new_jobs) == 1 else "new postings"
            msg = (
                f"✅ Job search complete — {len(new_jobs)} {noun} checked, "
                f"none matched your criteria."
            )
            try:
                _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
            except Exception as exc:
                print(f"Telegram notification error: {exc}", file=sys.stderr)

        print("Done.", flush=True)

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        _send_error_notification(exc)
        raise


if __name__ == "__main__":
    main()
