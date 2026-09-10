"""Keep the checked-in configuration examples aligned with the catalog."""
import json
from pathlib import Path

from job_search import settings
from job_search.settings import OPTION_CATALOG, validate_settings_path


ROOT = Path(__file__).resolve().parents[1]
TOML_EXAMPLE = ROOT / "job_search.example.toml"
ENV_EXAMPLE = ROOT / "deployment.env.example"
DOCS = ROOT / "docs" / "configuration.md"


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
            assert "consumed_by: {}".format(descriptor["consumed_by"]) in line
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


def test_configuration_docs_keep_data_only_and_runtime_boundaries_explicit():
    docs = DOCS.read_text(encoding="utf-8")

    for command in ("--describe-config", "--validate-config", "--check-config"):
        assert command in docs
    assert "No environment override, customization" in docs
    assert "trusted `job_search_config.py`" in docs
    assert "criteria_file" in docs and "evaluator does not read it" in docs
    assert "cv_bullet_selection.txt" in docs
    assert "smallest resulting number" in docs
    assert "Broad geography is never guessed" in docs
    assert "United Kingdom, Switzerland" in docs
    assert "does not auto-shrink" in docs
    assert "blank-entry smoke check keeps" in docs
    assert "legacy personal defaults" in docs
    assert "not active in PR1" in docs
    assert "canonicalizes it to uppercase" in docs
    assert "explicitly local role remains eligible" in docs
    assert "LinkedIn Israel" in docs
    assert "including remote" in docs
    assert "Residency never implies work" in docs
