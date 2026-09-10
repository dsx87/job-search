"""Keep the checked-in configuration examples aligned with the catalog."""
import json
from pathlib import Path

from job_search import settings
from job_search.settings import OPTION_CATALOG, validate_settings_path


ROOT = Path(__file__).resolve().parents[1]
TOML_EXAMPLE = ROOT / "job_search.example.toml"
ENV_EXAMPLE = ROOT / "deployment.env.example"
DOCS = ROOT / "docs" / "configuration.md"


def _annotation_value(value):
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def test_toml_example_is_valid_and_covers_every_file_supported_catalog_option():
    loaded = validate_settings_path(TOML_EXAMPLE)
    expected = {
        spec.dotted_key for spec in OPTION_CATALOG if not spec.environment_only
    }

    assert {
        origin.source for origin in loaded.origins.values() if origin.kind == "file"
    } == expected
    assert loaded.values["version"] == settings.SETTINGS_VERSION


def test_examples_explain_environment_only_options_without_placing_them_in_toml():
    toml = TOML_EXAMPLE.read_text(encoding="utf-8")
    env = ENV_EXAMPLE.read_text(encoding="utf-8")

    for spec in OPTION_CATALOG:
        if not spec.environment_only:
            continue
        assert "# {} —".format(spec.dotted_key) in toml
        start = toml.index(spec.dotted_key)
        nearby_comment = "\n".join(toml[start:].split("\n", 4)[:4])
        assert "env only" in nearby_comment
        for variable in spec.env:
            assert variable in env


def test_examples_list_every_catalog_environment_alias_and_active_host_control():
    toml = TOML_EXAMPLE.read_text(encoding="utf-8")
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    metadata = settings.option_metadata()

    for option in metadata["options"].values():
        for variable in option["env"]:
            assert variable in toml or variable in env

    for name, descriptor in metadata["environment"].items():
        assert "# {} |".format(name) in env
        if descriptor.get("status") == "active":
            line = next(
                line for line in env.splitlines()
                if line.startswith("# {} |".format(name))
            )
            assert "status: active" in line
            assert "consumed_by: {}".format(
                _annotation_value(descriptor["consumed_by"])
            ) in line


def test_catalog_annotations_cover_every_required_documentation_field():
    example = TOML_EXAMPLE.read_text(encoding="utf-8")
    environment = ENV_EXAMPLE.read_text(encoding="utf-8")
    labels = (
        "purpose:", "type:", "default:", "allowed:", "env:",
        "dependencies:", "effect:",
    )

    metadata = settings.option_metadata()
    for key, option in metadata["options"].items():
        line = next(
            item for item in example.splitlines()
            if item.startswith("# {} |".format(key))
        )
        assert all(label in line for label in labels)
        assert "purpose: {}".format(option["description"]) in line
        assert "type: {}".format(option["type"]) in line
        assert "default: {}".format(
            json.dumps(option["default"], separators=(",", ":"))
        ) in line
        assert "env: {}".format(" / ".join(option["env"]) or "none") in line
        if option["allowed_values"]:
            assert "allowed: {}".format(
                ", ".join(option["allowed_values"])
            ) in line
        if option["constraints"]:
            assert "constraints:" in line
    for key, descriptor in metadata["environment"].items():
        line = next(
            item for item in environment.splitlines()
            if item.startswith("# {} |".format(key))
        )
        assert all(label in line for label in labels)
        assert "purpose: {}".format(descriptor["description"]) in line
        assert "type: {}".format(descriptor["type"]) in line
        assert "default: {}".format(
            json.dumps(descriptor["default"], separators=(",", ":"))
        ) in line
        assert "env: {}".format(descriptor["env"]) in line
        if descriptor["sensitive"]:
            assert "sensitive" in line
        if descriptor.get("status"):
            assert "status: {}".format(descriptor["status"]) in line
            assert "consumed_by: {}".format(
                _annotation_value(descriptor["consumed_by"])
            ) in line
        if descriptor.get("constraints"):
            for value in descriptor["constraints"].values():
                assert _annotation_value(value) in line
        for field in (
            "absent_behavior", "absent_path", "empty_behavior", "execution",
        ):
            if field in descriptor:
                assert "{}: {}".format(field, descriptor[field]) in line


def test_constraint_annotations_explain_the_catalog_rules():
    metadata = settings.option_metadata()["options"]
    example = TOML_EXAMPLE.read_text(encoding="utf-8")

    def annotation(key):
        return next(
            line for line in example.splitlines()
            if line.startswith("# {} |".format(key))
        )

    country = metadata["candidate.max_pages_by_country"]["constraints"]
    assert country["key_format"] == "ISO 3166-1 alpha-2"
    assert {"DE", "FR", "GB"} <= set(country["allowed_keys"])
    assert {"XK", "ZZ"}.isdisjoint(country["allowed_keys"])
    assert "canonicalized uppercase; XK/ZZ reject" in annotation(
        "candidate.max_pages_by_country"
    )

    region = metadata["candidate.max_pages_by_region"]["constraints"]
    assert region == {"allowed_keys": ["EU"], "value_type": "positive_int"}
    assert "only EU is a valid key" in annotation("candidate.max_pages_by_region")

    checks = metadata["policy.check_order"]["constraints"]
    assert checks["unique"] is True
    assert set(checks["allowed_values"]) >= {"language", "role_match"}
    assert "identifiers are known and unique" in annotation("policy.check_order")

    for key in ("search.sources_enable", "search.sources_disable"):
        source_constraint = metadata[key]["constraints"]
        assert source_constraint["unique"] is True
        assert "linkedin-israel" in source_constraint["allowed_values"]
        assert "unique known sources" in annotation(key)

    skills = metadata["search.skill_include_groups"]["constraints"]
    assert skills["item_type"] == "string_tuple"
    assert skills["item_min_items"] == 1
    assert "every inner skill-alternative group has at least one term" in annotation(
        "search.skill_include_groups"
    )

    placeholders = metadata["candidate.private_placeholders"]["constraints"]
    assert placeholders == {
        "value_format": {"regex": "^[A-Z_][A-Z0-9_]*$"},
        "resolved_value_sensitive": True,
    }
    placeholder_note = annotation("candidate.private_placeholders")
    assert "^[A-Z_][A-Z0-9_]*$" in placeholder_note
    assert "resolved values are sensitive while the map is non-secret" in placeholder_note

    output = metadata["settings.output_mode"]["constraints"]
    assert output == {
        "requires_when": {"telegram": {"settings.output_cv_mode": "required"}}
    }
    assert "output_cv_mode" in annotation("settings.output_mode")

    prompt_dir = metadata["settings.prompt_dir"]["constraints"]
    assert prompt_dir == {"requires": {"settings.prompt_revision": "nonempty"}}
    assert "prompt_revision" in annotation("settings.prompt_dir")

    config_ref = settings.option_metadata()["environment"]["CONFIG_REF"]
    assert config_ref["constraints"] == {"regex": "^[0-9a-f]{40}$"}
    ref_line = next(
        line for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith("# CONFIG_REF |")
    )
    assert "lowercase 40-hex commit SHA" in ref_line
    ssh_key = settings.option_metadata()["environment"]["CONFIG_SSH_KEY"]
    assert ssh_key["default"] == "$HOME/.ssh/job_search_config_ed25519"
    assert "# CONFIG_SSH_KEY |" in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_deployment_template_only_activates_private_checkout_coordinates():
    active = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert active == {"CONFIG_REPOSITORY", "CONFIG_REF", "CONFIG_SSH_KEY"}
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "# JOB_SEARCH_SETTINGS_FILE=.private-config/job-search-config/job_search.toml" in example
    assert "# OUTPUT_CV_MODE=disabled" in example


def test_configuration_docs_keep_data_only_and_runtime_boundaries_explicit():
    docs = DOCS.read_text(encoding="utf-8")

    for command in ("--describe-config", "--validate-config", "--check-config"):
        assert command in docs
    assert "pure data validation" in docs
    assert "`job_search_config.py` is a rare, reviewed local-host escape hatch" in docs
    assert "criteria_file" in docs and "neither evaluator context nor executable policy" in docs
    assert "cv_bullet_selection.txt" in docs
    assert "smallest result for a multi-location role" in docs
    assert "and EMEA do not imply EU" in docs
    assert "United Kingdom, Switzerland" in docs
    assert "auto-shrink, repair" in docs
    assert "blank-entry smoke warning retains" in docs
    assert "There is no implicit personal profile" in docs
    assert "at least one explicit `[search]` setting" in docs
    assert "Search and evaluation commands accept blank CV identity and path fields" in docs
    assert "`rendered_base_file`; tailored rendering" in docs
    assert ".private-config/job-search-config" in docs
    assert "--check-only" in docs
    assert "mode-600 `.deployment.env`" in docs
    assert "`.env` stays a separate mode-600 credential file" in docs
    assert "not active in PR1" not in docs
    assert "canonicalize to uppercase" in docs
    assert "explicitly local role remains eligible" in docs
    assert "Israel are specialized explicit-only sources" in docs
    assert "including remote" in docs
    assert "Residency never supplies authorization" in docs
    assert "TUI can open, browse, and mark the existing local job history" in docs
