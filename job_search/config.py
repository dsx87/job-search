"""Configuration: frozen dataclasses + from_env(), plus prompt/file loaders.

The module-level constants reproduce the original flat-module globals exactly,
so defaults are unchanged. PipelineConfig wraps them for explicit injection;
run.py builds a config once and threads it through the stages. The scraper side
reads its two constants (HTTP_TIMEOUT_SECONDS, MAX_WORKERS) directly — a
ScraperConfig wrapper existed here until 2026-07-25 but was never constructed
outside its own test, so it was removed rather than left as decoration.
"""
import os
from dataclasses import dataclass

# ── Scraper defaults ──────────────────────────────────────────────────────────
HTTP_TIMEOUT_SECONDS = 30
MAX_WORKERS = 8

# Ceiling on a single HTTP response body. An unbounded read() on the 512 MB Pi is
# one oversized or chunk-streaming response away from an OOM-kill mid-run
# (finding N10). 8 MB is ~20x the largest legitimate job-board payload observed
# (a full JSON feed runs a few hundred KB).
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

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
# Optional user-defined digest sections (job_search.digest.section_config). The
# file is absent by default, and an absent file renders today's ungrouped
# digest — the feature is opt-in and costs nothing until it exists.
SECTIONS_FILE = "sections.py"

# ── LLM defaults (generic, scheme-based providers) ─────────────────────────────
# A provider is a wire-protocol *scheme* (gemini | openai | anthropic) + model +
# API key (+ optional base). Switching providers is a config edit — no code
# change. See job_search.llm.clients for the schemes and the factory.
#
# Primary model choice (recorded deliberately, 2026-07-25): gemini-2.5-flash is
# kept over the 3.x lineage because 2.5 proved steadier on this workload — see
# commit 08a12a2. That is a quality call, not an oversight, and it carries a
# dated risk: Google has scheduled 2.5-flash for shutdown (see
# LLM_MODEL_SHUTDOWN_DATES). Revisit before that date; if a 3.x successor has
# since become steady enough, switch LLM_PRIMARY_MODEL here (or pin it per-runner
# via the LLM_PRIMARY_MODEL env var / workflow variable) and update the README +
# docs/deploy-rpi.md examples alongside it.
LLM_PRIMARY_SCHEME = "gemini"
LLM_PRIMARY_MODEL = "gemini-2.5-flash"
LLM_FALLBACK_SCHEME = "openai"          # OpenAI-compatible: also Groq, DeepSeek, xAI, … via api_base
LLM_FALLBACK_MODEL = "gpt-5.4-mini"

# Announced provider shutdown dates (ISO) for models this repo defaults to. A
# retired model does not degrade — it starts answering 404, which is neither
# retryable nor a circuit-break status, so without this the only symptom would be
# a doomed primary request per job. Surfaced ahead of time in the run log and the
# digest footer (see llm.clients.model_shutdown_warning) so the date is visible
# while there is still time to act.
LLM_MODEL_SHUTDOWN_DATES = {
    "gemini-2.5-flash": "2026-10-16",
}
# How far ahead of a shutdown date the warning starts appearing.
LLM_MODEL_SHUTDOWN_WARN_DAYS = 120

# Transient HTTP statuses a provider retries internally before giving up.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Statuses that mean "this model does not exist here" — a retired or misspelled
# model. Nothing about the next request will change that, so re-attempting per
# job is pure waste: the primary is disabled for the rest of the run after one
# loud message.
LLM_MODEL_REJECT_STATUS = {404}

# Statuses that mean "this REQUEST was rejected". 400 is deliberately NOT in the
# set above: Gemini returns INVALID_ARGUMENT for per-request conditions (an
# over-long prompt, a schema the model dislikes, a body it won't accept), and
# this pipeline feeds it arbitrary scraped job descriptions. Letting one
# pathological posting move every remaining job onto the fallback would be a
# cost and quality shift triggered by a single bad input, so a 400 falls back for
# that request only — but it is still called out once, because a bad model name
# surfaces as a 400 on some providers.
LLM_REQUEST_REJECT_STATUS = {400}

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
# same safe try/except pattern as LATEX_MAX_WORKERS (read at import so a
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

# pdflatex is CPU/IO-heavy; cap concurrent compilations independently of the
# tailor pool so a large TAILOR_WORKERS can't spawn many parallel pdflatex runs
# and starve a small runner. Read at import so compile.py's module-level
# semaphore honors it; a malformed/non-positive value falls back to the default.
# LATEX_MAX_WORKERS is the current name; the legacy XELATEX_MAX_WORKERS is still
# honored as a fallback so an existing Pi .env with the old name keeps working.
# Take the first candidate that parses as an int, so a malformed new value still
# falls back to a valid legacy value (rather than masking it) before the default.
LATEX_MAX_WORKERS = 2
for _latex_workers_env in ("LATEX_MAX_WORKERS", "XELATEX_MAX_WORKERS"):
    _latex_workers_raw = os.environ.get(_latex_workers_env, "").strip()
    if _latex_workers_raw:
        try:
            LATEX_MAX_WORKERS = int(_latex_workers_raw)
            break
        except ValueError:
            continue
if LATEX_MAX_WORKERS < 1:
    LATEX_MAX_WORKERS = 2

# Minimum job-description length before we trust it for evaluation or tailoring.
MIN_JOB_TEXT_LEN = 200

# ── Telegram delivery limits / retries ────────────────────────────────────────
# Telegram's sendMessage text cap. Enforced in notify.telegram at the client
# boundary: an overlong message is a 400, and some callers have already marked
# their jobs seen by the time it raises (finding N6).
TELEGRAM_MAX_MESSAGE_CHARS = 4096

# Backoff between transient-failure retries of a Telegram send. Delivery is the
# most expensive thing in the run to lose — in digest mode one blip on the single
# ZIP send defers every fit for a day and re-pays the LLM + pdflatex cost
# (finding N8) — but the ladder still has to fit inside the CI job cap, so it
# matches LLM_RETRY_BACKOFF rather than going longer.
TELEGRAM_RETRY_BACKOFF = (2, 8, 20)

# Upper bound on a `retry_after` Telegram asks us to honor on a 429; beyond this
# the fixed ladder above is used instead, so a hostile/absurd value can't stall
# the run.
TELEGRAM_RETRY_AFTER_CAP = 60


def _split_csv(raw: str) -> tuple:
    """Parse a comma-separated env list into a lowercased/stripped/de-empty tuple.

    e.g. ``" A, b ,,"`` → ``("a", "b")``. Tuples (not lists) so the frozen
    PipelineConfig can carry them as safe defaults.
    """
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _non_empty_env(name: str, default: str) -> str:
    """Return a stripped environment override, or the default when it is blank."""
    return os.environ.get(name, "").strip() or default


def _positive_int_env(name: str, default: int) -> int:
    """Return a positive int from the environment, falling back on anything else.

    Every other env read in this module is deliberately crash-proof; the worker
    counts were the exception — ``int(os.environ["EVAL_WORKERS"])`` raised on
    garbage, and a ``0`` or negative value reached
    ``ThreadPoolExecutor(max_workers=0)`` and raised there instead (finding 12).
    A misconfigured tuning knob should not take down a scheduled run.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"Warning: {name}={raw!r} is not an integer; using {default}.")
        return default
    if value < 1:
        print(f"Warning: {name}={value} must be >= 1; using {default}.")
        return default
    return value


@dataclass(frozen=True)
class PipelineConfig:
    # Generic, role-based LLM config: a primary provider and an optional
    # fallback, each a scheme + model + key (+ optional base). A blank api_base
    # resolves to the scheme's default in the provider factory.
    llm_primary_scheme: str = LLM_PRIMARY_SCHEME
    llm_primary_model: str = LLM_PRIMARY_MODEL
    llm_primary_api_key: str = ""
    llm_primary_api_base: str = ""
    # ``none`` is an explicit opt-in for trusted local OpenAI-compatible
    # servers (for example LM Studio) and requires a non-default API base. The
    # default keeps today's Bearer auth.
    llm_primary_auth_mode: str = "bearer"
    llm_fallback_scheme: str = LLM_FALLBACK_SCHEME
    llm_fallback_model: str = LLM_FALLBACK_MODEL
    llm_fallback_api_key: str = ""
    llm_fallback_api_base: str = ""
    llm_fallback_auth_mode: str = "bearer"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    eval_workers: int = EVAL_WORKERS
    tailor_workers: int = TAILOR_WORKERS
    seen_jobs_file: str = SEEN_JOBS_FILE
    criteria_file: str = CRITERIA_FILE
    cv_tailoring_prompt_file: str = CV_TAILORING_PROMPT_FILE
    base_tex_file: str = BASE_TEX_FILE
    rendered_base_file: str = OUT_PDF_FILE
    sections_file: str = SECTIONS_FILE
    # Source selection: names forced ON (adds default-off sources like
    # linkedin-guest) / forced OFF (removes default-on sources). Empty tuples →
    # today's default-on set, so CI with no env is unchanged. See
    # sources.fetch.select_sources for the resolution rule.
    sources_enable: tuple = ()
    sources_disable: tuple = ()
    # STATE_SYNC=1 → git-sync seen_jobs.json (pull before / push after) around
    # run_daily, sharing the dedup baseline via the orphan `state` branch.
    state_sync: bool = False
    # DIGEST_DELIVERY (default on) → deliver one ZIP per run (HTML dashboard +
    # tailored CVs) instead of a stream of per-job Telegram messages. Set
    # DIGEST_DELIVERY=0 to fall back to the legacy per-job delivery path.
    digest_delivery: bool = True
    # TELEGRAPH_ACCESS_TOKEN → publish each digest as a telegra.ph page and keep
    # a rolling index page, so the whole run is ONE Telegram message: the page
    # link plus the CV archive password. Telegraph cannot host files, so one
    # AES-256 protected ZIP of ordinary tailored PDFs is uploaded to x0.at and
    # linked from the page. Empty (the default) means the run sends the ZIP,
    # so a runner without the secret behaves exactly as it did before — and so
    # does one where encryption or an upload fails. Layers under
    # DIGEST_DELIVERY: the legacy per-job path never consults it.
    telegraph_access_token: str = ""

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
            llm_primary_auth_mode=_non_empty_env("LLM_PRIMARY_AUTH_MODE", "bearer").lower(),
            llm_fallback_scheme=_non_empty_env("LLM_FALLBACK_SCHEME", LLM_FALLBACK_SCHEME),
            llm_fallback_model=_non_empty_env("LLM_FALLBACK_MODEL", LLM_FALLBACK_MODEL),
            llm_fallback_api_key=_non_empty_env(
                "LLM_FALLBACK_API_KEY", os.environ.get("OPENAI_API_KEY", "")
            ),
            llm_fallback_api_base=_non_empty_env("LLM_FALLBACK_API_BASE", ""),
            llm_fallback_auth_mode=_non_empty_env("LLM_FALLBACK_AUTH_MODE", "bearer").lower(),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            seen_jobs_file=_non_empty_env("SEEN_JOBS_FILE", SEEN_JOBS_FILE),
            criteria_file=_non_empty_env("CRITERIA_FILE", CRITERIA_FILE),
            cv_tailoring_prompt_file=_non_empty_env(
                "CV_TAILORING_PROMPT_FILE", CV_TAILORING_PROMPT_FILE
            ),
            base_tex_file=_non_empty_env("BASE_TEX_FILE", BASE_TEX_FILE),
            rendered_base_file=_non_empty_env("OUT_PDF_FILE", OUT_PDF_FILE),
            sections_file=_non_empty_env("SECTIONS_FILE", SECTIONS_FILE),
            eval_workers=_positive_int_env("EVAL_WORKERS", EVAL_WORKERS),
            tailor_workers=_positive_int_env("TAILOR_WORKERS", TAILOR_WORKERS),
            sources_enable=_split_csv(os.environ.get("SOURCES_ENABLE", "")),
            sources_disable=_split_csv(os.environ.get("SOURCES_DISABLE", "")),
            state_sync=os.environ.get("STATE_SYNC", "") == "1",
            digest_delivery=os.environ.get("DIGEST_DELIVERY", "1") != "0",
            telegraph_access_token=os.environ.get("TELEGRAPH_ACCESS_TOKEN", ""),
        )


# ── Prompt / file loaders ──────────────────────────────────────────────────────
def load_criteria(path: str = CRITERIA_FILE) -> str:
    with open(path) as f:
        return f.read()


def load_tailoring_instructions(path: str = CV_TAILORING_PROMPT_FILE) -> str:
    """Extract STEP 3 through (not including) BASE LaTeX TEMPLATE."""
    with open(path) as f:
        content = f.read()
    start = content.index("## STEP 3")
    end = content.index("## BASE LaTeX TEMPLATE")
    return content[start:end].strip()


def load_base_tex(path: str = BASE_TEX_FILE) -> str:
    with open(path) as f:
        return f.read()
