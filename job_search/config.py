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

# Wall-clock budget (seconds) for the whole fetch stage. Any source still
# running when this elapses is abandoned so a single throttled source
# (historically LinkedIn via jobspy, whose per-description requests get
# rate-limited) can't hang the run past the CI job timeout. Comfortably longer
# than a healthy full fetch (~2-3 min) yet well under the 30-min CI cap, leaving
# room for the evaluate/tailor/deliver stages. Overridable per-run via the
# SCRAPE_BUDGET_SECONDS env var (read at call time in fetch_jobs, not here, so a
# malformed value can't crash the scraper CLI at import).
SCRAPE_BUDGET_SECONDS = 600

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


def _split_csv(raw: str) -> tuple:
    """Parse a comma-separated env list into a lowercased/stripped/de-empty tuple.

    e.g. ``" A, b ,,"`` → ``("a", "b")``. Tuples (not lists) so the frozen
    PipelineConfig can carry them as safe defaults.
    """
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


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
    # Source selection: names forced ON (adds default-off sources like
    # linkedin-guest) / forced OFF (removes default-on sources). Empty tuples →
    # today's default-on set, so CI with no env is unchanged. See
    # sources.fetch.select_sources for the resolution rule.
    sources_enable: tuple = ()
    sources_disable: tuple = ()
    # STATE_SYNC=1 → git-sync seen_jobs.json (pull before / push after) around
    # run_daily, sharing the dedup baseline via the orphan `state` branch.
    state_sync: bool = False

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        return cls(
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            qwen_api_key=os.environ.get("QWEN_API_KEY", ""),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            eval_workers=int(os.environ.get("EVAL_WORKERS", str(EVAL_WORKERS))),
            tailor_workers=int(os.environ.get("TAILOR_WORKERS", str(TAILOR_WORKERS))),
            sources_enable=_split_csv(os.environ.get("SOURCES_ENABLE", "")),
            sources_disable=_split_csv(os.environ.get("SOURCES_DISABLE", "")),
            state_sync=os.environ.get("STATE_SYNC", "") == "1",
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
