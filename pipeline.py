#!/usr/bin/env python3
"""
Job search pipeline: fetch → deduplicate → LLM evaluate → tailor resume → Telegram notify.

Required environment variables:
  GEMINI_API_KEY       — Gemini API key
  TELEGRAM_BOT_TOKEN   — Telegram bot token
  TELEGRAM_CHAT_ID     — Target chat/user ID

Run once with no seen_jobs.json to seed state (marks all current jobs seen, sends nothing).
"""

import json
import os
import re
import sys
import time
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_JOBS_FILE = "seen_jobs.json"
CRITERIA_FILE = "criteria.md"
CV_TAILORING_PROMPT_FILE = "cv_tailoring_prompt.md"
BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"

GEMINI_MODEL = "gemini-2.0-flash"
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
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())

        return result["candidates"][0]["content"]["parts"][0]["text"]


# ── State persistence ────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


def load_seen_jobs():
    """Returns a set of seen URL keys, or None if the state file doesn't exist (seed mode)."""
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
    return m.group(1) if m else text


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

def process_job(gemini: GeminiClient, criteria: str, tailoring_instructions: str, base_tex: str, job: dict) -> None:
    title = job.get("title", "?")
    company = job.get("company", "?")
    print(f"  Evaluating: {title} at {company}", flush=True)

    try:
        evaluation = evaluate_job(gemini, criteria, job)
    except Exception as exc:
        print(f"    Evaluation error: {exc}", file=sys.stderr)
        return

    if not evaluation.get("fit"):
        print(f"    Skip — {evaluation.get('reason', '')}")
        return

    print(f"    Fit! Tailoring resume...", flush=True)

    tex_source = None
    try:
        tex_source = tailor_resume(gemini, tailoring_instructions, base_tex, job)
    except Exception as exc:
        print(f"    Tailoring error: {exc}", file=sys.stderr)

    message = _format_notification(job, evaluation)
    try:
        _tg_send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
    except Exception as exc:
        print(f"    Telegram message error: {exc}", file=sys.stderr)

    if tex_source:
        filename = f"igor_pivnyk_cv_{_company_slug(company)}.tex"
        try:
            _tg_send_document(
                TELEGRAM_BOT_TOKEN,
                TELEGRAM_CHAT_ID,
                filename,
                tex_source.encode("utf-8"),
                caption=f"Tailored CV — {title} at {company}",
            )
        except Exception as exc:
            print(f"    Telegram document error: {exc}", file=sys.stderr)


# ── Main ─────────────────────────────────────────────────────────────────────

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
        print(f"Test mode: processing one job without touching seen_jobs.json.")
        process_job(gemini, criteria, tailoring_instructions, base_tex, d)
        print("Done.", flush=True)
        return

    seen_raw = load_seen_jobs()
    seed_mode = seen_raw is None
    seen = seen_raw if seen_raw is not None else set()

    if seed_mode:
        print(f"Seed mode: marking {len(raw_jobs)} existing job(s) as seen. No notifications sent.")
        for j in raw_jobs:
            seen.add(normalize_url(j.url))
        save_seen_jobs(seen)
        print("State saved. Future runs will notify about new jobs.")
        return

    new_jobs = []
    for j in raw_jobs:
        key = normalize_url(j.url)
        if key not in seen:
            d = job_to_dict(j)
            d["description"] = j.description
            new_jobs.append(d)
            seen.add(key)

    # Persist updated seen set before any LLM calls — a crash mid-run won't re-process jobs.
    save_seen_jobs(seen)
    print(f"Found {len(new_jobs)} new job(s).", flush=True)

    for job in new_jobs:
        process_job(gemini, criteria, tailoring_instructions, base_tex, job)
        time.sleep(1)

    print("Done.", flush=True)


if __name__ == "__main__":
    main()
