from dataclasses import replace
from pathlib import Path

import pytest

from job_search.composition import (
    Components,
    ConfigurationError,
    load_components,
    redacted_configuration,
)
from job_search.components import (
    DefaultOutputBackend,
    DefaultOutputRenderer,
    default_components,
)
from job_search.config import PipelineConfig


def _configured_env(monkeypatch, path):
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(path))


def test_missing_optional_default_uses_builtin_components(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)

    components = load_components(PipelineConfig(), command="list")

    assert isinstance(components, Components)
    assert isinstance(components.output_renderer, DefaultOutputRenderer)
    assert isinstance(components.output_backend, DefaultOutputBackend)


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
        "def allow_all(job):\n"
        "    return True\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, candidate_filter=allow_all)\n",
        encoding="utf-8",
    )

    components = load_components(PipelineConfig(), command="list")

    assert components.candidate_filter.__name__ == "allow_all"
    assert components.candidate_filter(object()) is True


def test_direct_module_loading_registers_module_for_dataclasses(tmp_path, monkeypatch):
    config_file = tmp_path / "dataclass_config.py"
    config_file.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass, replace\n"
        "@dataclass\n"
        "class Filter:\n"
        "    revision: str = 'dataclass-filter-v1'\n"
        "    def __call__(self, job): return True\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, candidate_filter=Filter())\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    components = load_components(PipelineConfig(), command="check")

    assert components.candidate_filter.revision == "dataclass-filter-v1"
    assert components.candidate_filter(object()) is True


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


def test_noop_config_returns_the_defaults_untouched(tmp_path, monkeypatch):
    config_file = tmp_path / "noop.py"
    config_file.write_text(
        "def configure(defaults, settings):\n"
        "    return defaults\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)
    settings = PipelineConfig()
    defaults = default_components(settings)

    components = load_components(settings, command="list", defaults=defaults)

    assert components is defaults


def test_in_place_config_mutation_rebuilds_dependent_defaults(tmp_path, monkeypatch):
    config_file = tmp_path / "in_place.py"
    config_file.write_text(
        "from job_search.components import CandidateProfile, DefaultPromptSet\n"
        "class Prompts(DefaultPromptSet):\n"
        "    revision = 'in-place-prompts-v1'\n"
        "def configure(defaults, settings):\n"
        "    defaults.prompts = Prompts()\n"
        "    defaults.profile = CandidateProfile(\n"
        "        display_name='Ada Example')\n"
        "    return defaults\n",
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)

    components = load_components(PipelineConfig(), command="list")

    assert components.cv_renderer.prompts is components.prompts
    assert components.cv_renderer.profile is components.profile
    assert components.cv_renderer.compiler.prompts is components.prompts
    assert components.cv_renderer.compiler.profile is components.profile


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
        "from job_search.output import FilesystemOutputBackend, PlainTextOutputRenderer\n"
        "def configure(defaults, settings):\n"
        "    return replace(defaults, output_renderer=PlainTextOutputRenderer(), "
        "output_backend=FilesystemOutputBackend({!r}, require_artifact=False))\n".format(
            str(tmp_path / "out")
        ),
        encoding="utf-8",
    )
    _configured_env(monkeypatch, config_file)
    # OUTPUT_MODE/OUTPUT_CV_MODE must agree with what the hatch built above —
    # the telegram-credentials and OUTPUT_MODE=telegram+disabled checks are
    # keyed on these settings, not on the swapped-in objects, until the
    # escape hatch gets a way to flip them itself (Runtime, C10).
    settings = PipelineConfig(
        llm_primary_scheme="openai",
        llm_primary_model="local-model",
        llm_primary_api_base="http://127.0.0.1:1234/v1",
        llm_primary_auth_mode="none",
        llm_primary_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        output_mode="plain",
        output_cv_mode="disabled",
    )

    components = load_components(settings, command="daily")

    assert components.output_backend.require_artifact is False


def test_no_auth_mode_is_rejected_for_non_openai_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(
        llm_primary_api_key="primary-key",
        llm_fallback_scheme="gemini",
        llm_fallback_auth_mode="none",
        llm_fallback_api_key="",
    )

    with pytest.raises(ConfigurationError, match="fallback.*openai"):
        load_components(settings, command="check")


def test_no_auth_fallback_requires_an_explicit_non_default_api_base(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(
        llm_fallback_scheme="openai",
        llm_fallback_auth_mode="none",
        llm_fallback_api_key="",
        llm_fallback_api_base="",
    )

    with pytest.raises(ConfigurationError, match="explicit non-default api_base"):
        load_components(settings, command="list")


def test_check_config_rejects_missing_required_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_SEARCH_CONFIG_FILE", raising=False)
    settings = PipelineConfig(criteria_file=str(tmp_path / "missing.md"))

    with pytest.raises(ConfigurationError, match="criteria_file"):
        load_components(settings, command="check")


def test_default_renderer_subclass_owns_its_inputs(tmp_path, monkeypatch):
    criteria = tmp_path / "criteria.md"
    criteria.write_text("criteria", encoding="utf-8")
    # The subclass below keeps a `.profile` around (it delegates to it for
    # nothing but attribute parity) but fully owns rendering. Preflight can
    # no longer tell the two apart by identity (reads_profile_sources is
    # gone), so it checks the same files any default-shaped profile would
    # need — write them even though this renderer never opens them itself.
    (tmp_path / "igor_pivnyk_cv_base_updated.tex").write_text("tex", encoding="utf-8")
    (tmp_path / "cv_tailoring_prompt.md").write_text(
        "## STEP 3\ninstructions\n## BASE LaTeX TEMPLATE\n", encoding="utf-8"
    )
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
