"""Coverage for job_search.runtime: build_runtime, the escape hatch, preflight."""
import pytest

from job_search.config import ConfigurationError, PipelineConfig
from job_search.runtime import Runtime, apply_user_config, build_runtime, redacted_settings


def _runtime(**overrides):
    values = dict(llm=None, prompts=None, cv_renderer=None, renderer=None, backend=None)
    values.update(overrides)
    return Runtime(**values)


def _cv_fixtures(tmp_path):
    (tmp_path / "criteria.md").write_text("criteria", encoding="utf-8")
    (tmp_path / "igor_pivnyk_cv_base_updated.tex").write_text("tex", encoding="utf-8")
    (tmp_path / "cv_tailoring_prompt.md").write_text(
        "## STEP 3\ninstructions\n## BASE LaTeX TEMPLATE\n", encoding="utf-8"
    )


# ── apply_user_config: the escape hatch itself ────────────────────────────────


def test_absent_config_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    runtime = _runtime()

    result = apply_user_config(runtime, PipelineConfig())

    assert result is runtime
    assert result.config_file == ""


def test_explicit_missing_config_file_raises(tmp_path, monkeypatch):
    missing = tmp_path / "missing.py"
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(missing))

    with pytest.raises(ConfigurationError, match="does not exist"):
        apply_user_config(_runtime(), PipelineConfig())


def test_empty_config_file_env_var_still_raises(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", "")

    with pytest.raises(ConfigurationError, match="empty"):
        apply_user_config(_runtime(), PipelineConfig())


def test_module_without_configure_raises(tmp_path, monkeypatch):
    config_file = tmp_path / "no_configure.py"
    config_file.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))

    with pytest.raises(ConfigurationError, match="configure"):
        apply_user_config(_runtime(), PipelineConfig())


def test_old_style_configure_gets_a_migration_error(tmp_path, monkeypatch):
    config_file = tmp_path / "old_style.py"
    config_file.write_text(
        "def configure(defaults, settings):\n    return defaults\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))

    with pytest.raises(ConfigurationError, match=r"configure\(runtime, settings\)"):
        apply_user_config(_runtime(), PipelineConfig())


def test_a_broken_hatch_module_raises_its_own_exception_type_raw(tmp_path, monkeypatch):
    config_file = tmp_path / "broken.py"
    config_file.write_text("raise RuntimeError('broken hatch')\n", encoding="utf-8")
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))

    with pytest.raises(RuntimeError, match="broken hatch"):
        apply_user_config(_runtime(), PipelineConfig())


def test_a_broken_configure_call_raises_its_own_exception_type_raw(tmp_path, monkeypatch):
    config_file = tmp_path / "broken_configure.py"
    config_file.write_text(
        "class MyOwnError(Exception):\n"
        "    pass\n"
        "def configure(runtime, settings):\n"
        "    raise MyOwnError('boom')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))

    with pytest.raises(Exception) as excinfo:
        apply_user_config(_runtime(), PipelineConfig())

    assert excinfo.type.__name__ == "MyOwnError"
    assert "boom" in str(excinfo.value)


def test_in_place_mutation_of_the_runtime_is_supported(tmp_path, monkeypatch):
    config_file = tmp_path / "hatch.py"
    config_file.write_text(
        "def configure(runtime, settings):\n"
        "    runtime.candidate_filter = lambda job: True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))
    runtime = _runtime()

    result = apply_user_config(runtime, PipelineConfig())

    # configure() above mutates in place and returns None; apply_user_config
    # falls back to the same object (``configure(...) or runtime``).
    assert result is runtime
    assert result.candidate_filter(object()) is True
    assert result.config_file == str(config_file)


def test_module_loading_registers_itself_for_dataclasses(tmp_path, monkeypatch):
    """Guards the sys.modules pre-registration a @dataclass hatch file needs."""
    config_file = tmp_path / "dataclass_config.py"
    config_file.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Filter:\n"
        "    revision: str = 'dataclass-filter-v1'\n"
        "    def __call__(self, job):\n"
        "        return True\n"
        "def configure(runtime, settings):\n"
        "    runtime.candidate_filter = Filter()\n"
        "    return runtime\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))

    rt = apply_user_config(_runtime(), PipelineConfig())

    assert rt.candidate_filter.revision == "dataclass-filter-v1"
    assert rt.candidate_filter(object()) is True


# ── build_runtime: the built-in graph + preflight ─────────────────────────────


def test_check_config_rejects_missing_required_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(criteria_file=str(tmp_path / "missing.md"))

    with pytest.raises(ConfigurationError, match="criteria_file"):
        build_runtime(settings, command="check")


def test_hatch_cv_less_backend_clears_flags_and_skips_cv_and_telegram_checks(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "criteria.md").write_text("criteria", encoding="utf-8")
    config_file = tmp_path / "hatch.py"
    config_file.write_text(
        "def configure(runtime, settings):\n"
        "    runtime.cv_required = False\n"
        "    runtime.needs_telegram = False\n"
        "    runtime.needs_base_tex = False\n"
        "    return runtime\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))
    # No base_tex/cv_tailoring_prompt file and no Telegram credentials exist
    # anywhere in tmp_path — build_runtime must not ask for them.
    settings = PipelineConfig(llm_primary_api_key="key")

    rt = build_runtime(settings, command="daily")

    assert rt.cv_required is False
    assert rt.needs_telegram is False
    assert rt.needs_base_tex is False


@pytest.mark.parametrize(
    ("output_mode", "renderer_cls", "expect_markup"),
    (
        ("telegram", "DefaultOutputRenderer", True),
        ("html", "HtmlOutputRenderer", False),
        ("plain", "PlainTextOutputRenderer", False),
    ),
)
def test_output_mode_selects_the_renderer_and_telegram_markup_flag(
    tmp_path, monkeypatch, output_mode, renderer_cls, expect_markup
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    _cv_fixtures(tmp_path)
    settings = PipelineConfig(
        output_mode=output_mode,
        output_dir=str(tmp_path / "out"),
        llm_primary_api_key="key",
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )

    rt = build_runtime(settings, command="check")

    assert type(rt.renderer).__name__ == renderer_cls
    assert rt.telegram_markup is expect_markup


# ── redacted_settings / --check-config ─────────────────────────────────────────


def test_redacted_settings_hides_secrets_and_reports_null_config_file(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    _cv_fixtures(tmp_path)
    settings = PipelineConfig(
        llm_primary_api_key="primary-secret",
        llm_fallback_api_key="fallback-secret",
        telegram_bot_token="telegram-secret",
        telegram_chat_id="123456",
        telegraph_access_token="telegraph-secret",
    )

    rt = build_runtime(settings, command="check")
    rendered = redacted_settings(settings, rt)

    for secret in (
        "primary-secret", "fallback-secret", "telegram-secret", "123456", "telegraph-secret",
    ):
        assert secret not in rendered
    assert "<redacted>" in rendered
    assert '"runtime"' in rendered
    assert '"config_file": null' in rendered


def test_redacted_settings_reports_which_hatch_ran(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cv_fixtures(tmp_path)
    config_file = tmp_path / "hatch.py"
    config_file.write_text(
        "def configure(runtime, settings):\n    return runtime\n", encoding="utf-8"
    )
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(config_file))
    settings = PipelineConfig(llm_primary_api_key="key")

    rt = build_runtime(settings, command="check")
    rendered = redacted_settings(settings, rt)

    assert rt.config_file == str(config_file)
    assert str(config_file) in rendered
