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

# ── LLM defaults (generic, scheme-based providers) ─────────────────────────────
# A provider is a wire-protocol *scheme* (gemini | openai | anthropic) + model +
# API key (+ optional base). Switching providers is a config edit — no code
# change. See job_search.llm.clients for the schemes and the factory.
LLM_PRIMARY_SCHEME = "gemini"
LLM_PRIMARY_MODEL = "gemini-2.5-flash"
LLM_FALLBACK_SCHEME = "openai"          # OpenAI-compatible: also Groq, DeepSeek, xAI, … via api_base
LLM_FALLBACK_MODEL = "gpt-5.4-mini"

# Transient HTTP statuses a provider retries internally before giving up.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Statuses that count toward the primary circuit-breaker (429 rate limit,
# 503 overloaded). A post-retry error with one of these codes increments the
# consecutive-failure counter; the primary is disabled for the run only once the
# counter reaches LLM_BREAKER_THRESHOLD, so a single transient blip recovers.
LLM_CIRCUIT_BREAK_STATUS = {429, 503}

# Shared retry backoff (seconds) between a provider's transient-error attempts.
# Deliberately short — the primary is in the hot path and the old [30,60,120]
# risked the 30-min CI job cap.
LLM_RETRY_BACKOFF = (2, 8, 20)

# Consecutive post-retry circuit-break failures before the primary is disabled
# for the rest of the run. Env-overridable via LLM_BREAKER_THRESHOLD, using the
# same safe try/except pattern as XELATEX_MAX_WORKERS (read at import so a
# malformed value can't crash the scraper CLI).
try:
    LLM_BREAKER_THRESHOLD = int(os.environ.get("LLM_BREAKER_THRESHOLD", "2"))
except ValueError:
    LLM_BREAKER_THRESHOLD = 2
if LLM_BREAKER_THRESHOLD < 1:
    LLM_BREAKER_THRESHOLD = 2

# The Anthropic Messages API requires max_tokens; other schemes ignore it.
# Env-overridable via ANTHROPIC_MAX_TOKENS.
try:
    ANTHROPIC_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096"))
except ValueError:
    ANTHROPIC_MAX_TOKENS = 4096
if ANTHROPIC_MAX_TOKENS < 1:
    ANTHROPIC_MAX_TOKENS = 4096

# Default concurrency for the staged pipeline. Per-run overrides come from the
# EVAL_WORKERS / TAILOR_WORKERS env vars, but they are read in
# PipelineConfig.from_env() — not at import time — so a malformed value can't
# crash the scraper CLI, which imports this module only for HTTP_TIMEOUT_SECONDS.
EVAL_WORKERS = 12
TAILOR_WORKERS = 8

# XeLaTeX is CPU/IO-heavy; cap concurrent compilations independently of the
# tailor pool so a large TAILOR_WORKERS can't spawn many parallel xelatex runs
# and starve a small runner. Read at import so compile.py's module-level
# semaphore honors it; a malformed/non-positive value falls back to the default.
try:
    XELATEX_MAX_WORKERS = int(os.environ.get("XELATEX_MAX_WORKERS", "2"))
except ValueError:
    XELATEX_MAX_WORKERS = 2
if XELATEX_MAX_WORKERS < 1:
    XELATEX_MAX_WORKERS = 2

# Minimum job-description length before we trust it for evaluation or tailoring.
MIN_JOB_TEXT_LEN = 200


def _split_csv(raw: str) -> tuple:
    """Parse a comma-separated env list into a lowercased/stripped/de-empty tuple.

    e.g. ``" A, b ,,"`` → ``("a", "b")``. Tuples (not lists) so the frozen
    PipelineConfig can carry them as safe defaults.
    """
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _non_empty_env(name: str, default: str) -> str:
    """Return a stripped environment override, or the default when it is blank."""
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class ScraperConfig:
    http_timeout_seconds: int = HTTP_TIMEOUT_SECONDS
    max_workers: int = MAX_WORKERS

    @classmethod
    def from_env(cls) -> "ScraperConfig":
        return cls()


@dataclass(frozen=True)
class PipelineConfig:
    # Generic, role-based LLM config: a primary provider and an optional
    # fallback, each a scheme + model + key (+ optional base). A blank api_base
    # resolves to the scheme's default in the provider factory.
    llm_primary_scheme: str = LLM_PRIMARY_SCHEME
    llm_primary_model: str = LLM_PRIMARY_MODEL
    llm_primary_api_key: str = ""
    llm_primary_api_base: str = ""
    llm_fallback_scheme: str = LLM_FALLBACK_SCHEME
    llm_fallback_model: str = LLM_FALLBACK_MODEL
    llm_fallback_api_key: str = ""
    llm_fallback_api_base: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
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
            # New LLM_* vars win; fall back to the legacy GEMINI_*/OPENAI_* names
            # so nothing hard-breaks mid-migration. Blank api_base → scheme
            # default (resolved in the provider factory).
            llm_primary_scheme=_non_empty_env("LLM_PRIMARY_SCHEME", LLM_PRIMARY_SCHEME),
            llm_primary_model=_non_empty_env(
                "LLM_PRIMARY_MODEL", _non_empty_env("GEMINI_MODEL", LLM_PRIMARY_MODEL)
            ),
            llm_primary_api_key=_non_empty_env(
                "LLM_PRIMARY_API_KEY", os.environ.get("GEMINI_API_KEY", "")
            ),
            llm_primary_api_base=_non_empty_env(
                "LLM_PRIMARY_API_BASE", _non_empty_env("GEMINI_API_BASE", "")
            ),
            llm_fallback_scheme=_non_empty_env("LLM_FALLBACK_SCHEME", LLM_FALLBACK_SCHEME),
            llm_fallback_model=_non_empty_env("LLM_FALLBACK_MODEL", LLM_FALLBACK_MODEL),
            llm_fallback_api_key=_non_empty_env(
                "LLM_FALLBACK_API_KEY", os.environ.get("OPENAI_API_KEY", "")
            ),
            llm_fallback_api_base=_non_empty_env("LLM_FALLBACK_API_BASE", ""),
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
