from dataclasses import replace
from pathlib import Path

import pytest

from job_search.composition import (
    Components,
    ConfigurationError,
    load_components,
    redacted_configuration,
)
from job_search.config import PipelineConfig


def _configured_env(monkeypatch, path):
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(path))


def test_missing_optional_default_uses_builtin_components(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)

    components = load_components(PipelineConfig(), command="list")

    assert isinstance(components, Components)
    assert components.output_renderer.kind == "telegram"
    assert components.output_backend.cv_mode == "required"


def test_explicit_missing_config_is_an_error(tmp_path, monkeypatch):
    missing = tmp_path / "missing.py"
    _configured_env(monkeypatch, missing)

    with pytest.raises(ConfigurationError, match="does not exist"):
        load_components(PipelineConfig(), command="check")


def test_present_default_config_is_loaded_by_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    Path("job_search_config.py").write_text(
        "from dataclasses import replace\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, candidate_filter=AllowAll())\n"
        "class AllowAll:\n"
        "    revision = 'custom-filter-v1'\n"
        "    def include(self, job): return True\n",
        encoding="utf-8",
    )

    components = load_components(PipelineConfig(), command="list")

    assert components.candidate_filter.revision == "custom-filter-v1"


def test_direct_module_loading_registers_module_for_dataclasses(tmp_path, monkeypatch):
    config_file = tmp_path / "dataclass_config.py"
    config_file.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass, replace\n"
        "@dataclass\n"
        "class Filter:\n"
        "    revision: str = 'dataclass-filter-v1'\n"
        "    def include(self, job): return True\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, candidate_filter=Filter())\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    components = load_components(PipelineConfig(), command="check")

    assert components.candidate_filter.revision == "dataclass-filter-v1"


def test_configure_receives_defaults_and_exact_settings(tmp_path, monkeypatch):
    config_file = tmp_path / "custom.py"
    config_file.write_text(
        "def configure(defaults, settings):\n"
        "    assert settings.criteria_file == 'chosen.md'\n"
        "    defaults._settings_seen = settings\n"
        "    return defaults\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)
    settings = PipelineConfig(criteria_file="chosen.md")

    components = load_components(settings, command="list")

    assert components._settings_seen is settings


@pytest.mark.parametrize(
    "source, message",
    [
        ("VALUE = 1\n", "configure"),
        ("def configure(defaults, settings): return None\n", "Components"),
        ("raise RuntimeError('broken config')\n", "broken config"),
    ],
)
def test_invalid_config_modules_fail_with_context(tmp_path, monkeypatch, source, message):
    config_file = tmp_path / "invalid.py"
    config_file.write_text(source, encoding="utf-8")
    _configured_env(monkeypatch, config_file)

    with pytest.raises(ConfigurationError, match=message):
        load_components(PipelineConfig(), command="check")


def test_renderer_backend_kind_mismatch_fails_validation(tmp_path, monkeypatch):
    config_file = tmp_path / "mismatch.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "class Renderer:\n"
        "    kind = 'html'\n"
        "    def render_notice(self, *a, **k): return ''\n"
        "    def render_fit(self, *a, **k): return ''\n"
        "    def render_digest(self, *a, **k): return ''\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, output_renderer=Renderer())\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    with pytest.raises(ConfigurationError, match="renderer kind"):
        load_components(PipelineConfig(), command="check")


def test_profile_must_be_a_candidate_profile(tmp_path, monkeypatch):
    config_file = tmp_path / "bad_profile.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, profile='not-a-profile')\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    with pytest.raises(ConfigurationError, match="CandidateProfile"):
        load_components(PipelineConfig(), command="check")


@pytest.mark.parametrize(
    "profile_args, message",
    [
        ("employer_order=(None,)", "employer_order"),
        ("employer_order=None", "employer_order"),
        ("forbidden_claim_patterns=None", "forbidden_claim_patterns"),
        ("private_placeholders={1: 'CV_PHONE'}", "private_placeholders"),
        ("private_placeholders=[]", "private_placeholders"),
    ],
)
def test_invalid_candidate_profile_values_fail_as_configuration_errors(
    tmp_path, monkeypatch, profile_args, message
):
    config_file = tmp_path / "bad_profile_values.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "from job_search.components import CandidateProfile\n"
        "def configure(defaults, settings):\n"
        "    profile = CandidateProfile({})\n".format(profile_args)
        + "    return replace(defaults, profile=profile)\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    with pytest.raises(ConfigurationError, match=message):
        load_components(PipelineConfig(), command="list")


def test_tailor_rejects_text_only_backend_during_validation(tmp_path, monkeypatch):
    config_file = tmp_path / "text_only.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "class Backend:\n"
        "    accepted_renderer_kinds = ('telegram',)\n"
        "    accepted_media_types = ()\n"
        "    cv_mode = 'disabled'\n"
        "    requires_telegram_credentials = False\n"
        "    def deliver_notice(self, *a, **k): pass\n"
        "    def deliver_fit(self, *a, **k): pass\n"
        "    def deliver_digest(self, *a, **k): pass\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, output_backend=Backend())\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    with pytest.raises(ConfigurationError, match="--tailor requires"):
        load_components(PipelineConfig(), command="tailor")


def test_redacted_configuration_hides_all_secret_values(monkeypatch):
    settings = PipelineConfig(
        llm_primary_api_key="primary-secret",
        llm_fallback_api_key="fallback-secret",
        telegram_bot_token="telegram-secret",
        telegram_chat_id="123456",
        telegraph_access_token="telegraph-secret",
    )
    components = load_components(settings, command="check")

    rendered = redacted_configuration(settings, components)

    for secret in (
        "primary-secret", "fallback-secret", "telegram-secret", "123456", "telegraph-secret"
    ):
        assert secret not in rendered
    assert "<redacted>" in rendered
    assert "output_renderer" in rendered


def test_custom_text_backend_needs_no_llm_key_or_telegram_credentials(tmp_path, monkeypatch):
    config_file = tmp_path / "plain.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "from job_search.output import PlainMessageBackend, PlainTextOutputRenderer\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, output_renderer=PlainTextOutputRenderer(), "
        "output_backend=PlainMessageBackend(lambda message: None))\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)
    settings = PipelineConfig(
        llm_primary_scheme="openai",
        llm_primary_model="local-model",
        llm_primary_api_base="http://127.0.0.1:1234/v1",
        llm_primary_auth_mode="none",
        llm_primary_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )

    components = load_components(settings, command="daily")

    assert components.output_backend.cv_mode == "disabled"


def test_no_auth_mode_is_rejected_for_non_openai_fallback(monkeypatch):
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(
        llm_primary_api_key="primary-key",
        llm_fallback_scheme="gemini",
        llm_fallback_auth_mode="none",
        llm_fallback_api_key="",
    )

    with pytest.raises(ConfigurationError, match="fallback.*openai"):
        load_components(settings, command="check")


def test_check_config_rejects_missing_required_files(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(criteria_file=str(tmp_path / "missing.md"))

    with pytest.raises(ConfigurationError, match="criteria_file"):
        load_components(settings, command="check")


def test_default_renderer_subclass_owns_its_inputs(tmp_path, monkeypatch):
    criteria = tmp_path / "criteria.md"
    criteria.write_text("criteria", encoding="utf-8")
    config_file = tmp_path / "renderer_subclass.py"
    config_file.write_text(
        "from dataclasses import replace\n"
        "from job_search.components import CVArtifact, DefaultCVRenderer\n"
        "class Renderer(DefaultCVRenderer):\n"
        "    def __init__(self, defaults):\n"
        "        self.profile = defaults.profile\n"
        "        self.compiler = defaults.cv_renderer.compiler\n"
        "    def render_tailored(self, llm, job, evaluation=None):\n"
        "        return CVArtifact('custom.pdf', 'application/pdf', b'PDF')\n"
        "    def render_base(self, llm=None):\n"
        "        return CVArtifact('custom.pdf', 'application/pdf', b'PDF')\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, cv_renderer=Renderer(defaults))\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)
    monkeypatch.chdir(tmp_path)
    settings = PipelineConfig(
        criteria_file=str(criteria),
        llm_primary_api_key="key",
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )

    components = load_components(settings, command="daily")

    assert type(components.cv_renderer).__name__ == "Renderer"
