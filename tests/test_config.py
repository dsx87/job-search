"""Characterization tests locking the config defaults (must not drift)."""
# --- module under test (repoint on migration) ---
from job_search import config
from job_search.config import ScraperConfig, PipelineConfig


def test_scraper_config_defaults():
    assert config.HTTP_TIMEOUT_SECONDS == 30
    assert config.MAX_WORKERS == 8
    sc = ScraperConfig.from_env()
    assert sc.http_timeout_seconds == 30
    assert sc.max_workers == 8


def test_pipeline_config_defaults():
    assert config.EVAL_WORKERS == 12
    assert config.TAILOR_WORKERS == 8
    # Generic, role-based LLM defaults.
    assert config.LLM_PRIMARY_SCHEME == "gemini"
    assert config.LLM_PRIMARY_MODEL == "gemini-2.5-flash"
    assert config.LLM_FALLBACK_SCHEME == "openai"
    assert config.LLM_FALLBACK_MODEL == "gpt-5.4-mini"
    assert config.RETRYABLE_STATUS == {429, 500, 502, 503, 504}
    assert config.LLM_CIRCUIT_BREAK_STATUS == {429, 503}
    assert config.LLM_RETRY_BACKOFF == (2, 8, 20)
    assert config.LLM_BREAKER_THRESHOLD == 2
    assert config.ANTHROPIC_MAX_TOKENS == 4096
    assert config.MIN_JOB_TEXT_LEN == 200


def test_pipeline_filenames():
    assert config.SEEN_JOBS_FILE == "seen_jobs.json"
    assert config.CRITERIA_FILE == "criteria.md"
    assert config.CV_TAILORING_PROMPT_FILE == "cv_tailoring_prompt.md"
    assert config.BASE_TEX_FILE == "igor_pivnyk_cv_base_updated.tex"
    assert config.OUT_PDF_FILE == "igor_pivnyk_cv_base_updated.pdf"


def test_pipeline_config_from_env_reads_keys(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_API_KEY", "p-key")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "f-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("EVAL_WORKERS", "5")
    pc = PipelineConfig.from_env()
    assert pc.llm_primary_api_key == "p-key"
    assert pc.llm_fallback_api_key == "f-key"
    assert pc.telegram_bot_token == "tok"
    assert pc.telegram_chat_id == "chat"
    assert pc.eval_workers == 5
    # defaults preserved for unset values
    assert pc.llm_primary_scheme == "gemini"
    assert pc.llm_primary_model == "gemini-2.5-flash"
    assert pc.llm_primary_api_base == ""  # blank → scheme default resolved in the factory
    assert pc.llm_fallback_scheme == "openai"
    assert pc.llm_fallback_model == "gpt-5.4-mini"
    assert pc.llm_fallback_api_base == ""


def test_pipeline_config_from_env_reads_provider_overrides(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_SCHEME", "  anthropic  ")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "  claude-haiku-4-5  ")
    monkeypatch.setenv("LLM_PRIMARY_API_BASE", "  https://primary.example/v1/  ")
    monkeypatch.setenv("LLM_FALLBACK_SCHEME", "  openai  ")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "  grok-4.3  ")
    monkeypatch.setenv("LLM_FALLBACK_API_BASE", "  https://api.x.ai/v1  ")

    pc = PipelineConfig.from_env()

    assert pc.llm_primary_scheme == "anthropic"
    assert pc.llm_primary_model == "claude-haiku-4-5"
    assert pc.llm_primary_api_base == "https://primary.example/v1/"
    assert pc.llm_fallback_scheme == "openai"
    assert pc.llm_fallback_model == "grok-4.3"
    assert pc.llm_fallback_api_base == "https://api.x.ai/v1"


def test_pipeline_config_from_env_uses_defaults_for_blank_overrides(monkeypatch):
    for name in (
        "LLM_PRIMARY_SCHEME", "LLM_PRIMARY_MODEL", "LLM_PRIMARY_API_BASE",
        "LLM_FALLBACK_SCHEME", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_API_BASE",
    ):
        monkeypatch.setenv(name, " \t ")

    pc = PipelineConfig.from_env()

    assert pc.llm_primary_scheme == config.LLM_PRIMARY_SCHEME
    assert pc.llm_primary_model == config.LLM_PRIMARY_MODEL
    assert pc.llm_primary_api_base == ""
    assert pc.llm_fallback_scheme == config.LLM_FALLBACK_SCHEME
    assert pc.llm_fallback_model == config.LLM_FALLBACK_MODEL
    assert pc.llm_fallback_api_base == ""


def test_pipeline_config_from_env_honors_legacy_gemini_openai_vars(monkeypatch):
    # Back-compat so nothing hard-breaks mid-migration: the primary key/model/base
    # fall back to the legacy GEMINI_* names, the fallback key to OPENAI_API_KEY.
    monkeypatch.delenv("LLM_PRIMARY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_API_BASE", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-g-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-legacy")
    monkeypatch.setenv("GEMINI_API_BASE", "https://legacy.example/models")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-o-key")

    pc = PipelineConfig.from_env()

    assert pc.llm_primary_api_key == "legacy-g-key"
    assert pc.llm_primary_model == "gemini-legacy"
    assert pc.llm_primary_api_base == "https://legacy.example/models"
    assert pc.llm_fallback_api_key == "legacy-o-key"


def test_pipeline_config_new_llm_vars_win_over_legacy(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_API_KEY", "new-key")
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "new-model")
    monkeypatch.setenv("GEMINI_MODEL", "legacy-model")

    pc = PipelineConfig.from_env()

    assert pc.llm_primary_api_key == "new-key"
    assert pc.llm_primary_model == "new-model"


def test_pipeline_config_selection_and_sync_defaults():
    """Default config = today's behavior: no forced sources, no state sync."""
    pc = PipelineConfig()
    assert pc.sources_enable == ()
    assert pc.sources_disable == ()
    assert pc.state_sync is False
    # from_env with nothing set is identical.
    pc_env = PipelineConfig.from_env()
    assert pc_env.sources_enable == ()
    assert pc_env.sources_disable == ()
    assert pc_env.state_sync is False


def test_split_csv_normalizes():
    assert config._split_csv(" A, b ,,") == ("a", "b")
    assert config._split_csv("") == ()
    assert config._split_csv("LinkedIn-Guest") == ("linkedin-guest",)


def test_pipeline_config_from_env_parses_source_lists(monkeypatch):
    monkeypatch.setenv("SOURCES_ENABLE", "linkedin-guest")
    monkeypatch.setenv("SOURCES_DISABLE", "linkedin-global, linkedin-israel")
    pc = PipelineConfig.from_env()
    assert pc.sources_enable == ("linkedin-guest",)
    assert pc.sources_disable == ("linkedin-global", "linkedin-israel")


def test_pipeline_config_from_env_state_sync_flag(monkeypatch):
    monkeypatch.setenv("STATE_SYNC", "1")
    assert PipelineConfig.from_env().state_sync is True
    for falsey in ("0", "true", ""):
        monkeypatch.setenv("STATE_SYNC", falsey)
        assert PipelineConfig.from_env().state_sync is False
    monkeypatch.delenv("STATE_SYNC", raising=False)
    assert PipelineConfig.from_env().state_sync is False


def test_loaders_read_repo_files():
    assert "iOS" in config.load_criteria() or len(config.load_criteria()) > 0
    base = config.load_base_tex()
    assert "\\documentclass" in base
    instr = config.load_tailoring_instructions()
    assert instr  # STEP 3 slice is non-empty
    assert "## BASE LaTeX TEMPLATE" not in instr  # sliced out


# ── audit order 8 — pdflatex worker limit constant ────────────────────────────
def test_latex_max_workers_default():
    assert config.LATEX_MAX_WORKERS == 2


def test_latex_max_workers_env_override(monkeypatch):
    """The constant is read from the LATEX_MAX_WORKERS env var at import time."""
    import importlib

    monkeypatch.setenv("LATEX_MAX_WORKERS", "4")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.LATEX_MAX_WORKERS == 4
    finally:
        # restore the module to its default state for any later tests
        monkeypatch.delenv("LATEX_MAX_WORKERS", raising=False)
        importlib.reload(config)


def test_latex_max_workers_legacy_env_fallback(monkeypatch):
    """A pre-migration .env that still sets XELATEX_MAX_WORKERS is honored as a
    fallback when LATEX_MAX_WORKERS is unset."""
    import importlib

    monkeypatch.delenv("LATEX_MAX_WORKERS", raising=False)
    monkeypatch.setenv("XELATEX_MAX_WORKERS", "3")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.LATEX_MAX_WORKERS == 3
    finally:
        monkeypatch.delenv("XELATEX_MAX_WORKERS", raising=False)
        importlib.reload(config)


def test_latex_max_workers_malformed_new_falls_back_to_legacy(monkeypatch):
    """A malformed LATEX_MAX_WORKERS must not mask a valid legacy XELATEX_MAX_WORKERS:
    the first env candidate that parses as an int wins, else the default."""
    import importlib

    monkeypatch.setenv("LATEX_MAX_WORKERS", "not-an-int")
    monkeypatch.setenv("XELATEX_MAX_WORKERS", "3")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.LATEX_MAX_WORKERS == 3
    finally:
        monkeypatch.delenv("LATEX_MAX_WORKERS", raising=False)
        monkeypatch.delenv("XELATEX_MAX_WORKERS", raising=False)
        importlib.reload(config)
