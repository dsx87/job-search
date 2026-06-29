"""Configuration: frozen dataclasses + from_env(), plus prompt/file loaders.

The module-level constants reproduce the original flat-module globals exactly,
so defaults are unchanged. ScraperConfig/PipelineConfig wrap them for explicit
injection; run.py builds a config once and threads it through the stages.
"""
import os
from dataclasses import dataclass

# ── Scraper defaults ──────────────────────────────────────────────────────────
HTTP_TIMEOUT_SECONDS = 30
MAX_WORKERS = 8

# ── File names (loaded relative to the working directory, as on CI) ────────────
SEEN_JOBS_FILE = "seen_jobs.json"
CRITERIA_FILE = "criteria.md"
CV_TAILORING_PROMPT_FILE = "cv_tailoring_prompt.md"
BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"
OUT_PDF_FILE = "igor_pivnyk_cv_base_updated.pdf"

# ── LLM defaults ───────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Qwen fallback (Alibaba DashScope, OpenAI-compatible endpoint).
QWEN_MODEL = "qwen-plus"
QWEN_API_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Status codes that trip the Gemini circuit-breaker (429 rate limit, 503 overloaded).
GEMINI_CIRCUIT_BREAK_STATUS = {429, 503}

# Default concurrency for the staged pipeline. Per-run overrides come from the
# EVAL_WORKERS / TAILOR_WORKERS env vars, but they are read in
# PipelineConfig.from_env() — not at import time — so a malformed value can't
# crash the scraper CLI, which imports this module only for HTTP_TIMEOUT_SECONDS.
EVAL_WORKERS = 12
TAILOR_WORKERS = 8

# Minimum job-description length before we trust it enough to tailor against.
MIN_JOB_TEXT_LEN = 200


@dataclass(frozen=True)
class ScraperConfig:
    http_timeout_seconds: int = HTTP_TIMEOUT_SECONDS
    max_workers: int = MAX_WORKERS

    @classmethod
    def from_env(cls) -> "ScraperConfig":
        return cls()


@dataclass(frozen=True)
class PipelineConfig:
    gemini_api_key: str = ""
    qwen_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    gemini_model: str = GEMINI_MODEL
    gemini_api_base: str = GEMINI_API_BASE
    qwen_model: str = QWEN_MODEL
    qwen_api_base: str = QWEN_API_BASE
    eval_workers: int = EVAL_WORKERS
    tailor_workers: int = TAILOR_WORKERS
    seen_jobs_file: str = SEEN_JOBS_FILE
    criteria_file: str = CRITERIA_FILE
    cv_tailoring_prompt_file: str = CV_TAILORING_PROMPT_FILE
    base_tex_file: str = BASE_TEX_FILE

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        return cls(
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            qwen_api_key=os.environ.get("QWEN_API_KEY", ""),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            eval_workers=int(os.environ.get("EVAL_WORKERS", str(EVAL_WORKERS))),
            tailor_workers=int(os.environ.get("TAILOR_WORKERS", str(TAILOR_WORKERS))),
        )


# ── Prompt / file loaders ──────────────────────────────────────────────────────
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
