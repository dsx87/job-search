"""Public settings catalog and TOML loader behavior."""
import json
import re
from pathlib import Path

import pytest

from job_search.config import PipelineConfig
from job_search.settings import (
    ConfigValidationError,
    OPTION_CATALOG,
    load_settings,
    option_metadata,
    validate_settings_path,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_option_metadata_is_json_serializable_and_covers_catalog():
    metadata = option_metadata()

    assert metadata["version"] == 1
    assert set(metadata["options"]) == {spec.dotted_key for spec in OPTION_CATALOG}
    assert json.loads(json.dumps(metadata))["version"] == 1
    assert metadata["options"]["settings.llm_primary_api_key"]["sensitive"] is True
    assert metadata["options"]["policy.excluded_industries"]["default"] == []
    assert metadata["environment"]["LINKEDIN_BUDGET_SECONDS"] == {
        "env": "LINKEDIN_BUDGET_SECONDS",
        "type": "positive_duration_seconds",
        "default": "max(60, SCRAPE_BUDGET_SECONDS * 0.85)",
        "sensitive": False,
        "environment_only": True,
        "supported_in_file": False,
        "description": "LinkedIn Guest source time budget; defaults from the fetch budget.",
    }
    assert metadata["environment"]["CONFIG_DEPLOY_KEY"]["sensitive"] is True
    assert metadata["environment"]["CONFIG_DEPLOY_KEY"]["status"] == "active"
    assert metadata["environment"]["CONFIG_DEPLOY_KEY"]["consumed_by"] == "github_actions"
    assert metadata["environment"]["CONFIG_REF"]["constraints"] == {
        "regex": "^[0-9a-f]{40}$"
    }
    assert metadata["environment"]["CONFIG_REPOSITORY"]["consumed_by"] == [
        "github_actions", "private_config_helper"
    ]
    assert metadata["environment"]["CONFIG_REPOSITORY"]["constraints"] == {
        "regex": "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
    }
    assert metadata["environment"]["CONFIG_SSH_KEY"] == {
        "env": "CONFIG_SSH_KEY",
        "type": "path",
        "default": "$HOME/.ssh/job_search_config_ed25519",
        "sensitive": False,
        "status": "active",
        "consumed_by": "private_config_helper",
        "constraints": {"file": True, "readable": True},
        "environment_only": True,
        "supported_in_file": False,
        "description": "Path to the read-only private configuration SSH key; the path itself is not secret.",
    }
    legacy_hook = metadata["environment"]["JOB_SEARCH_CONFIG_FILE"]
    assert legacy_hook["default"] == "job_search_config.py"
    assert legacy_hook["absent_behavior"] == "optionally_load_default_path"
    assert legacy_hook["empty_behavior"] == "error"
    assert legacy_hook["execution"] == "trusted_python"
    assert metadata["options"]["candidate.max_pages_by_country"]["constraints"][
        "key_format"
    ] == "ISO 3166-1 alpha-2"
    assert metadata["options"]["candidate.max_pages_by_region"]["constraints"][
        "allowed_keys"
    ] == ["EU"]
    assert "language" in metadata["options"]["policy.check_order"]["constraints"][
        "allowed_values"
    ]
    assert "remotive" in metadata["options"]["search.sources_enable"]["constraints"][
        "allowed_values"
    ]
    assert metadata["options"]["search.skill_include_groups"]["constraints"][
        "item_min_items"
    ] == 1
    placeholders = metadata["options"]["candidate.private_placeholders"]
    assert placeholders["sensitive"] is False
    assert placeholders["constraints"] == {
        "value_format": {"regex": "^[A-Z_][A-Z0-9_]*$"},
        "resolved_value_sensitive": True,
    }


def test_metadata_and_no_file_defaults_do_not_import_a_toml_parser(monkeypatch):
    import job_search.settings as settings

    monkeypatch.setattr(
        settings, "_toml_parser", lambda: pytest.fail("TOML parser was imported")
    )

    assert option_metadata()["version"] == 1
    assert load_settings(environ={}).path is None


def test_validate_settings_path_reads_file_without_environment_overrides(tmp_path, monkeypatch):
    settings_path = _write(
        tmp_path / "settings.toml",
        """[settings]
version = 1
llm_primary_model = "from-file"
""",
    )
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "from-env")

    loaded = validate_settings_path(settings_path)

    assert loaded.values["llm_primary_model"] == "from-file"
    assert loaded.origins["llm_primary_model"].kind == "file"
    assert loaded.origins["llm_primary_model"].source == "settings.llm_primary_model"


def test_load_settings_precedence_is_explicit_then_env_then_file_then_default(
    tmp_path, monkeypatch
):
    settings_path = _write(
        tmp_path / "settings.toml",
        """[settings]
version = 1
llm_primary_model = "from-file"
""",
    )
    monkeypatch.setenv("JOB_SEARCH_SETTINGS_FILE", str(settings_path))
    monkeypatch.setenv("GEMINI_MODEL", "legacy-env")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "env")

    loaded = load_settings(overrides={"llm_primary_model": "explicit"})

    assert loaded.values["llm_primary_model"] == "explicit"
    assert loaded.origins["llm_primary_model"].kind == "explicit"
    assert loaded.origins["llm_primary_model"].source == "llm_primary_model"


def test_toml_input_paths_are_relative_to_file_and_output_paths_to_cwd(tmp_path):
    settings_dir = tmp_path / "config"
    settings_dir.mkdir()
    settings_path = _write(
        settings_dir / "settings.toml",
        """[settings]
version = 1
criteria_file = "criteria.md"
output_dir = "generated"

[candidate]
base_tex_file = "base.tex"
rendered_base_file = "base.pdf"
""",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    loaded = load_settings(
        environ={"JOB_SEARCH_SETTINGS_FILE": str(settings_path)}, cwd=run_dir
    )

    assert loaded.values["criteria_file"] == str(settings_dir / "criteria.md")
    assert loaded.values["base_tex_file"] == str(settings_dir / "base.tex")
    assert loaded.values["output_dir"] == str(run_dir / "generated")
    assert loaded.values["rendered_base_file"] == str(run_dir / "base.pdf")


@pytest.mark.parametrize(
    "content, needle",
    [
        ("[settings]\nversion = 1\nunknown = true\n", "settings.unknown"),
        ("[settings]\nversion = 2\n", "settings.version"),
        ("[settings]\nversion = 1\n[candidate]\nmax_pages = 0\n", "candidate.max_pages"),
        ("[settings]\nversion = 1\n[candidate]\nmax_pages_by_country = { usa = 2 }\n", "candidate.max_pages_by_country"),
        ("[settings]\nversion = 1\n[candidate]\nmax_pages_by_region = { US = 2 }\n", "candidate.max_pages_by_region"),
    ],
)
def test_strict_validation_names_the_invalid_key(tmp_path, content, needle):
    settings_path = _write(tmp_path / "settings.toml", content)

    with pytest.raises(ConfigValidationError, match=needle):
        validate_settings_path(settings_path)


def test_pipeline_config_from_env_includes_file_candidate_values(tmp_path, monkeypatch):
    settings_path = _write(
        tmp_path / "settings.toml",
        """[settings]
version = 1

[candidate]
display_name = "Ada Lovelace"
employer_order = ["First", "Second"]
private_placeholders = { "((EMAIL))" = "CV_EMAIL" }
residency_countries = ["il"]
work_authorization_countries = ["DE", "US"]
max_pages = 2
max_pages_by_country = { DE = 4 }
max_pages_by_region = { EU = 3 }
""",
    )
    monkeypatch.setenv("JOB_SEARCH_SETTINGS_FILE", str(settings_path))

    config = PipelineConfig.from_env()

    assert config.cv_display_name == "Ada Lovelace"
    assert config.candidate.display_name == "Ada Lovelace"
    assert config.candidate.employer_order == ("First", "Second")
    assert dict(config.candidate.private_placeholders) == {"((EMAIL))": "CV_EMAIL"}
    assert config.candidate.residency_countries == ("IL",)
    assert config.candidate.work_authorization_countries == ("DE", "US")
    assert config.candidate.max_pages == 2
    assert dict(config.candidate.max_pages_by_country) == {"DE": 4}
    assert dict(config.candidate.max_pages_by_region) == {"EU": 3}


@pytest.mark.parametrize(
    "section, assignment, needle",
    [
        ("candidate", 'forbidden_claim_patterns = ["["]', "invalid regex"),
        ("candidate", 'employer_order = ["Acme", "acme"]', "duplicate identifiers"),
        ("candidate", 'private_placeholders = { "((EMAIL))" = "email" }', "environment variable"),
        ("search", 'sources_enable = ["not-a-source"]', "unknown source"),
        ("search", 'sources_enable = ["remotive", "remotive"]', "duplicate identifiers"),
        ("policy", 'check_order = ["language", "language"]', "duplicate check"),
        ("policy", 'max_local_office_days = 8', "invalid integer"),
    ],
)
def test_strict_reference_validation_rejects_invalid_values(
    tmp_path, section, assignment, needle
):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\n[{}]\n{}\n".format(section, assignment),
    )

    with pytest.raises(ConfigValidationError, match=needle):
        validate_settings_path(settings_path)


def test_credentials_are_environment_only(tmp_path):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\nllm_primary_api_key = \"not-here\"\n",
    )

    with pytest.raises(ConfigValidationError, match="environment-only"):
        validate_settings_path(settings_path)


@pytest.mark.parametrize(
    "key, dotted_key",
    [
        (key, spec.dotted_key)
        for spec in OPTION_CATALOG
        if spec.environment_only
        for key in (spec.field, spec.dotted_key)
    ],
)
def test_explicit_overrides_cannot_bypass_environment_only_settings(key, dotted_key):
    with pytest.raises(ConfigValidationError, match=re.escape(dotted_key)):
        load_settings(overrides={key: "attempted override"})


def test_duplicate_toml_key_identifies_the_setting(tmp_path):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\noutput_mode = \"plain\"\noutput_mode = \"html\"\n",
    )

    with pytest.raises(ConfigValidationError, match="settings.output_mode"):
        validate_settings_path(settings_path)


@pytest.mark.parametrize(
    "content, dotted_path",
    [
        (
            "[settings]\nversion = 1\n[candidate]\n"
            "max_pages_by_country = { DE = 1, DE = 2 }\n",
            "candidate.max_pages_by_country.DE",
        ),
        ("[settings]\nversion = 1\n[search]\n[search]\n", "search"),
    ],
)
def test_duplicate_toml_forms_include_a_dotted_path(tmp_path, content, dotted_path):
    settings_path = _write(tmp_path / "settings.toml", content)

    with pytest.raises(ConfigValidationError, match=re.escape(dotted_path)):
        validate_settings_path(settings_path)


@pytest.mark.parametrize(
    "assignment",
    [
        "max_pages_by_country = { DE = true }",
        "max_pages_by_country = { DE = 1.5 }",
        "max_pages_by_region = { EU = false }",
        "max_pages_by_region = { EU = 2.5 }",
    ],
)
def test_page_limit_maps_require_positive_integer_values(tmp_path, assignment):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\n[candidate]\n{}\n".format(assignment),
    )

    with pytest.raises(ConfigValidationError, match="candidate.max_pages_by"):
        validate_settings_path(settings_path)


@pytest.mark.parametrize(
    "assignment",
    [
        "max_pages_by_country = { ZZ = 2 }",
        "max_pages_by_country = { XK = 2 }",
        'residency_countries = ["ZZ"]',
        'work_authorization_countries = ["XK"]',
    ],
)
def test_country_references_require_real_iso_alpha2_codes(tmp_path, assignment):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\n[candidate]\n{}\n".format(assignment),
    )

    with pytest.raises(ConfigValidationError, match="invalid ISO alpha-2"):
        validate_settings_path(settings_path)


@pytest.mark.parametrize(
    "assignment",
    [
        'eval_workers = "2"',
        "eval_workers = 2.0",
        "eval_workers = true",
    ],
)
def test_toml_integer_settings_do_not_coerce_strings_floats_or_booleans(tmp_path, assignment):
    settings_path = _write(
        tmp_path / "settings.toml", "[settings]\nversion = 1\n{}\n".format(assignment)
    )

    with pytest.raises(ConfigValidationError, match="settings.eval_workers"):
        validate_settings_path(settings_path)


def test_cross_field_constraints_are_checked_without_runtime_side_effects(tmp_path):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\nprompt_dir = \"prompts\"\n",
    )

    with pytest.raises(ConfigValidationError, match="settings.prompt_revision"):
        validate_settings_path(settings_path)


def test_skill_groups_must_not_contain_empty_alternatives(tmp_path):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\n[search]\nskill_include_groups = [[]]\n",
    )

    with pytest.raises(ConfigValidationError, match=r"search\.skill_include_groups\[0\]"):
        validate_settings_path(settings_path)


def test_selected_toml_rejects_conflicting_effective_source_lists(tmp_path):
    settings_path = _write(
        tmp_path / "settings.toml",
        "[settings]\nversion = 1\n[search]\nsources_enable = [\"remotive\"]\n",
    )

    with pytest.raises(ConfigValidationError, match="search.sources_enable"):
        load_settings(
            environ={
                "JOB_SEARCH_SETTINGS_FILE": str(settings_path),
                "SOURCES_DISABLE": "remotive",
            }
        )


def test_legacy_environment_keeps_source_enable_precedence_without_a_file():
    loaded = load_settings(
        environ={"SOURCES_ENABLE": "remotive", "SOURCES_DISABLE": "remotive"}
    )

    assert loaded.values["sources_enable"] == ("remotive",)
    assert loaded.values["sources_disable"] == ("remotive",)


def test_no_file_candidate_defaults_remain_generic(monkeypatch):
    monkeypatch.delenv("JOB_SEARCH_SETTINGS_FILE", raising=False)
    monkeypatch.delenv("CV_DISPLAY_NAME", raising=False)

    config = PipelineConfig.from_env()

    assert config.candidate.display_name == ""
    assert config.setting_origins["display_name"]["kind"] == "default"
    assert config.setting_origins["display_name"]["source"] == "default"
