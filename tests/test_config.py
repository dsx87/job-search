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
    assert config.GEMINI_MODEL == "gemini-2.5-flash"
    assert config.GEMINI_API_BASE == "https://generativelanguage.googleapis.com/v1beta/models"
    assert config.QWEN_MODEL == "qwen-plus"
    assert config.QWEN_API_BASE == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert config.RETRYABLE_STATUS == {429, 500, 502, 503, 504}
    assert config.GEMINI_CIRCUIT_BREAK_STATUS == {429, 503}
    assert config.MIN_JOB_TEXT_LEN == 200


def test_pipeline_filenames():
    assert config.SEEN_JOBS_FILE == "seen_jobs.json"
    assert config.CRITERIA_FILE == "criteria.md"
    assert config.CV_TAILORING_PROMPT_FILE == "cv_tailoring_prompt.md"
    assert config.BASE_TEX_FILE == "igor_pivnyk_cv_base_updated.tex"
    assert config.OUT_PDF_FILE == "igor_pivnyk_cv_base_updated.pdf"


def test_pipeline_config_from_env_reads_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("EVAL_WORKERS", "5")
    pc = PipelineConfig.from_env()
    assert pc.gemini_api_key == "g-key"
    assert pc.telegram_bot_token == "tok"
    assert pc.telegram_chat_id == "chat"
    assert pc.eval_workers == 5
    # defaults preserved for unset values
    assert pc.gemini_model == "gemini-2.5-flash"
    assert pc.qwen_model == "qwen-plus"


def test_loaders_read_repo_files():
    assert "iOS" in config.load_criteria() or len(config.load_criteria()) > 0
    base = config.load_base_tex()
    assert "\\documentclass" in base
    instr = config.load_tailoring_instructions()
    assert instr  # STEP 3 slice is non-empty
    assert "## BASE LaTeX TEMPLATE" not in instr  # sliced out
