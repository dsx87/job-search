"""Versioned, side-effect-free configuration catalog and TOML loader.

This module owns parsing and validation only.  It never starts a pipeline,
imports optional providers, or reads environment variables at import time.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from .location.countries import ISO_COUNTRY_CODES

SETTINGS_VERSION = 1


class ConfigValidationError(ValueError):
    """A settings file or override is structurally invalid."""


@dataclass(frozen=True)
class SettingSpec:
    section: str
    key: str
    field: str
    value_type: str
    default: Any
    description: str
    env: Tuple[str, ...] = ()
    sensitive: bool = False
    path_kind: str = ""
    environment_only: bool = False

    @property
    def dotted_key(self) -> str:
        return "{}.{}".format(self.section, self.key)


@dataclass(frozen=True)
class SettingOrigin:
    kind: str
    source: str


@dataclass(frozen=True)
class LoadedSettings:
    """Validated flat values plus the winning source of each value."""

    values: Mapping[str, Any]
    origins: Mapping[str, SettingOrigin]
    path: Optional[Path]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "origins", MappingProxyType(dict(self.origins)))


def _spec(section, key, value_type, default, description, *, env=(), sensitive=False, path_kind="", environment_only=False):
    return SettingSpec(section, key, key, value_type, default, description, tuple(env), sensitive, path_kind, environment_only)


# Keep this catalog declarative: documentation and a future describe-config
# command consume the same metadata as the loader.
OPTION_CATALOG = (
    _spec("settings", "version", "version", SETTINGS_VERSION, "Settings file format version."),
    _spec("settings", "llm_primary_scheme", "string", "gemini", "Primary LLM wire protocol.", env=("LLM_PRIMARY_SCHEME",)),
    _spec("settings", "llm_primary_model", "string", "gemini-2.5-flash", "Primary LLM model.", env=("LLM_PRIMARY_MODEL", "GEMINI_MODEL")),
    _spec("settings", "llm_primary_api_key", "string", "", "Primary LLM credential.", env=("LLM_PRIMARY_API_KEY", "GEMINI_API_KEY"), sensitive=True, environment_only=True),
    _spec("settings", "llm_primary_api_base", "string", "", "Primary LLM endpoint override.", env=("LLM_PRIMARY_API_BASE", "GEMINI_API_BASE")),
    _spec("settings", "llm_primary_auth_mode", "string", "bearer", "Primary LLM authentication mode.", env=("LLM_PRIMARY_AUTH_MODE",)),
    _spec("settings", "llm_fallback_scheme", "string", "openai", "Fallback LLM wire protocol.", env=("LLM_FALLBACK_SCHEME",)),
    _spec("settings", "llm_fallback_model", "string", "gpt-5.4-mini", "Fallback LLM model.", env=("LLM_FALLBACK_MODEL",)),
    _spec("settings", "llm_fallback_api_key", "string", "", "Fallback LLM credential.", env=("LLM_FALLBACK_API_KEY", "OPENAI_API_KEY"), sensitive=True, environment_only=True),
    _spec("settings", "llm_fallback_api_base", "string", "", "Fallback LLM endpoint override.", env=("LLM_FALLBACK_API_BASE",)),
    _spec("settings", "llm_fallback_auth_mode", "string", "bearer", "Fallback LLM authentication mode.", env=("LLM_FALLBACK_AUTH_MODE",)),
    _spec("settings", "telegram_bot_token", "string", "", "Telegram delivery credential.", env=("TELEGRAM_BOT_TOKEN",), sensitive=True, environment_only=True),
    _spec("settings", "telegram_chat_id", "string", "", "Telegram destination chat.", env=("TELEGRAM_CHAT_ID",), sensitive=True, environment_only=True),
    _spec("settings", "eval_workers", "positive_int", 12, "Concurrent evaluation workers.", env=("EVAL_WORKERS",)),
    _spec("settings", "tailor_workers", "positive_int", 8, "Concurrent tailoring workers.", env=("TAILOR_WORKERS",)),
    _spec("settings", "latex_max_workers", "positive_int", 2, "Concurrent LaTeX workers.", env=("LATEX_MAX_WORKERS", "XELATEX_MAX_WORKERS"), environment_only=True),
    _spec("settings", "llm_breaker_threshold", "positive_int", 2, "Primary LLM circuit-break threshold.", env=("LLM_BREAKER_THRESHOLD",), environment_only=True),
    _spec("settings", "anthropic_max_tokens", "positive_int", 4096, "Anthropic response token limit.", env=("ANTHROPIC_MAX_TOKENS",), environment_only=True),
    _spec("settings", "scrape_budget_seconds", "positive_int", 600, "Fetch-stage wall-clock limit.", env=("SCRAPE_BUDGET_SECONDS",)),
    _spec("settings", "seen_jobs_file", "string", "seen_jobs.json", "Persistent job-state file.", env=("SEEN_JOBS_FILE",), path_kind="output"),
    _spec("settings", "criteria_file", "string", "criteria.md", "Evaluation criteria input file.", env=("CRITERIA_FILE",), path_kind="input"),
    _spec("settings", "cv_tailoring_prompt_file", "string", "cv_tailoring_prompt.md", "Tailoring-instructions input file.", env=("CV_TAILORING_PROMPT_FILE",), path_kind="input"),
    _spec("settings", "sections_file", "string", "sections.py", "Optional digest sections input file.", env=("SECTIONS_FILE",), path_kind="input"),
    _spec("settings", "prompt_dir", "string", "", "Prompt override directory.", env=("PROMPT_DIR",), path_kind="input"),
    _spec("settings", "prompt_revision", "string", "", "Prompt override revision.", env=("PROMPT_REVISION",)),
    _spec("settings", "output_mode", "string", "telegram", "Output backend mode.", env=("OUTPUT_MODE",)),
    _spec("settings", "output_dir", "string", "", "Filesystem output directory.", env=("OUTPUT_DIR",), path_kind="output"),
    _spec("settings", "output_cv_mode", "string", "required", "CV output mode.", env=("OUTPUT_CV_MODE",)),
    _spec("settings", "latex_engine", "string", "pdflatex", "LaTeX executable.", env=("LATEX_ENGINE",)),
    _spec("settings", "state_sync", "bool", False, "Synchronize state with git.", env=("STATE_SYNC",)),
    _spec("settings", "digest_delivery", "bool", True, "Deliver a digest archive.", env=("DIGEST_DELIVERY",)),
    _spec("settings", "telegraph_access_token", "string", "", "Telegraph credential.", env=("TELEGRAPH_ACCESS_TOKEN",), sensitive=True, environment_only=True),
    _spec("search", "sources_enable", "string_tuple", (), "Sources explicitly enabled.", env=("SOURCES_ENABLE",)),
    _spec("search", "sources_disable", "string_tuple", (), "Sources explicitly disabled.", env=("SOURCES_DISABLE",)),
    _spec("search", "role_include_terms", "string_tuple", (), "Role-title terms to include."),
    _spec("search", "role_exclude_terms", "string_tuple", (), "Role-title terms to exclude."),
    _spec("search", "skill_include_groups", "string_tuple_tuple", (), "Required skill alternatives."),
    _spec("search", "location_exclude_terms", "string_tuple", (), "Location terms to exclude."),
    _spec("search", "relocation_regions", "string_tuple", ("eu", "ca", "us"), "Relocation regions."),
    _spec("search", "max_age_days", "nonnegative_int", 30, "Maximum posting age."),
    _spec("search", "remote_allowed", "bool", True, "Include remote jobs."),
    _spec("search", "relocation_allowed", "bool", True, "Include relocation jobs."),
    _spec("search", "search_terms", "string_tuple", (), "General provider-neutral search terms."),
    _spec("search", "query_locations", "string_tuple", (), "General provider-neutral query locations."),
    _spec("search", "results_per_query", "positive_int", 15, "Requested results per provider query."),
    _spec("candidate", "max_pages", "positive_int", 1, "Default tailored-CV page limit.", env=("CV_MAX_PAGES",)),
    _spec("candidate", "max_pages_by_country", "country_limit_map", {}, "Country-specific tailored-CV page limits."),
    _spec("candidate", "max_pages_by_region", "eu_region_limit_map", {}, "EU-only region tailored-CV page limits."),
    _spec("candidate", "display_name", "string", "", "Candidate name supplied to tailoring prompts.", env=("CV_DISPLAY_NAME",)),
    _spec("candidate", "filename_prefix", "string", "", "Tailored-CV filename prefix.", env=("CV_FILENAME_PREFIX",)),
    _spec("candidate", "base_tex_file", "string", "", "Base CV LaTeX input.", env=("BASE_TEX_FILE",), path_kind="input"),
    _spec("candidate", "rendered_base_file", "string", "", "Rendered base-CV output.", env=("OUT_PDF_FILE",), path_kind="output"),
    _spec("candidate", "employer_order", "string_tuple", (), "Fixed CV employment order."),
    _spec("candidate", "forbidden_claim_patterns", "string_tuple", (), "Patterns forbidden in tailored CVs."),
    _spec("candidate", "private_placeholders", "string_map", {}, "CV placeholders mapped to environment variables."),
    _spec("candidate", "residency_countries", "country_tuple", (), "Countries of legal residency."),
    _spec("candidate", "work_authorization_countries", "country_tuple", (), "Countries with work authorization."),
    _spec("policy", "check_order", "check_order", ("language", "role_match", "excluded_industry", "excluded_platform_focus", "minimum_seniority", "local_office_attendance", "remote_location_residency", "nonremote_nonpermanent_employment", "remote_fact_residency", "nonremote_work_authorization", "nonremote_sponsorship", "nonremote_arrangement"), "Ordered policy check identifiers."),
    _spec("policy", "require_english", "bool", True, "Require English job descriptions."),
    _spec("policy", "local_language_exempt", "bool", True, "Exempt local roles from language policy."),
    _spec("policy", "excluded_industries", "string_tuple", (), "Industries rejected by policy."),
    _spec("policy", "excluded_platform_focuses", "string_tuple", (), "Platform focuses rejected by policy."),
    _spec("policy", "rejected_seniority", "string_tuple", (), "Seniority levels rejected by policy."),
    _spec("policy", "max_local_office_days", "office_days", 5, "Maximum local onsite days per week."),
    _spec("policy", "allow_sponsorship_override", "bool", True, "Allow sponsorship to override residency restrictions."),
    _spec("policy", "allowed_languages", "string_tuple", ("english",), "Allowed job-description languages."),
    _spec("policy", "preferred_working_hours", "string_tuple", (), "Preferred working-hours notes."),
)

_BY_DOTTED = {spec.dotted_key: spec for spec in OPTION_CATALOG}
_BY_FIELD = {spec.field: spec for spec in OPTION_CATALOG}
_SECTIONS = {spec.section for spec in OPTION_CATALOG}
_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")
_ALLOWED_VALUES = {
    "llm_primary_scheme": {"gemini", "openai", "anthropic"},
    "llm_fallback_scheme": {"gemini", "openai", "anthropic"},
    "llm_primary_auth_mode": {"bearer", "none"},
    "llm_fallback_auth_mode": {"bearer", "none"},
    "output_mode": {"telegram", "html", "plain"},
    "output_cv_mode": {"required", "disabled"},
    "relocation_regions": {"eu", "ca", "au", "us"},
    "excluded_platform_focuses": {"ios_macos", "cross_platform", "other", "unknown"},
    "rejected_seniority": {"junior", "mid", "senior", "lead", "unknown"},
}
_ENVIRONMENT_METADATA = {
    "JOB_SEARCH_SETTINGS_FILE": {
        "type": "path", "default": "", "sensitive": False,
        "description": "Selects the versioned TOML file when nonempty.",
    },
    "JOB_SEARCH_CONFIG_FILE": {
        "type": "path", "default": "job_search_config.py", "sensitive": False,
        "absent_behavior": "optionally_load_default_path",
        "absent_path": "job_search_config.py",
        "empty_behavior": "error",
        "execution": "trusted_python",
        "description": "Optional trusted executable Python composition hook; an absent variable checks the default path, while an explicit empty value is an error.",
    },
    "JOB_SEARCH_CONFIG_PY": {
        "type": "string", "default": "", "sensitive": True,
        "status": "inactive", "consumed_by": None,
        "replacement": "pinned_private_config_checkout",
        "description": "Legacy Actions transport name retained for compatibility; current private-config workflows do not consume it.",
    },
    "SECTIONS_PY": {
        "type": "string", "default": "", "sensitive": False,
        "status": "inactive", "consumed_by": None,
        "replacement": "pinned_private_config_checkout",
        "description": "Legacy Actions transport name retained for compatibility; current private-config workflows do not consume it.",
    },
    "LINKEDIN_BUDGET_SECONDS": {
        "type": "positive_duration_seconds",
        "default": "max(60, SCRAPE_BUDGET_SECONDS * 0.85)",
        "sensitive": False,
        "description": "LinkedIn Guest source time budget; defaults from the fetch budget.",
    },
    "PERSONAL_RUNS_ENABLED": {
        "type": "boolean", "default": None, "sensitive": False,
        "status": "active", "consumed_by": "github_actions",
        "description": "GitHub Actions host control for personal scheduled runs.",
    },
    "CONFIG_REPOSITORY": {
        "type": "repository", "default": None, "sensitive": False,
        "status": "active", "consumed_by": ["github_actions", "private_config_helper"],
        "constraints": {"regex": "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"},
        "description": "Private configuration repository in owner/repository form, consumed by GitHub Actions and the local helper.",
    },
    "CONFIG_REF": {
        "type": "git_ref", "default": None, "sensitive": False,
        "status": "active", "consumed_by": ["github_actions", "private_config_helper"],
        "constraints": {"regex": "^[0-9a-f]{40}$"},
        "description": "Private configuration revision consumed by GitHub Actions and the local helper; must be a lowercase 40-hex commit SHA.",
    },
    "CONFIG_SSH_KEY": {
        "type": "path", "default": "$HOME/.ssh/job_search_config_ed25519", "sensitive": False,
        "status": "active", "consumed_by": "private_config_helper",
        "constraints": {"file": True, "readable": True},
        "description": "Path to the read-only private configuration SSH key; the path itself is not secret.",
    },
    "CONFIG_DEPLOY_KEY": {
        "type": "ssh_private_key", "default": None, "sensitive": True,
        "status": "active", "consumed_by": "github_actions",
        "description": "GitHub Actions secret deploy key for the configuration repository.",
    },
}


def option_metadata() -> dict:
    """Return a JSON-safe, versioned description of every supported option."""
    return {
        "version": SETTINGS_VERSION,
        "environment": {
            name: {
                "env": name,
                "environment_only": True,
                "supported_in_file": False,
                **descriptor,
            }
            for name, descriptor in _ENVIRONMENT_METADATA.items()
        },
        "options": {
            spec.dotted_key: {
                "section": spec.section,
                "key": spec.key,
                "type": spec.value_type,
                "default": _metadata_value(spec.default),
                "description": spec.description,
                "env": list(spec.env),
                "sensitive": spec.sensitive,
                "environment_only": spec.environment_only,
                "supported_in_file": not spec.environment_only,
                "allowed_values": sorted(_ALLOWED_VALUES.get(spec.field, ())),
                "constraints": _option_constraints(spec),
            }
            for spec in OPTION_CATALOG
        },
    }


def _option_constraints(spec: SettingSpec) -> dict:
    if spec.field == "max_pages_by_country":
        return {
            "allowed_keys": sorted(ISO_COUNTRY_CODES),
            "key_format": "ISO 3166-1 alpha-2",
            "value_type": "positive_int",
        }
    if spec.field == "max_pages_by_region":
        return {"allowed_keys": ["EU"], "value_type": "positive_int"}
    if spec.field == "check_order":
        return {"allowed_values": sorted(_CHECK_IDS), "unique": True}
    if spec.field in ("sources_enable", "sources_disable"):
        return {"allowed_values": sorted(_KNOWN_SOURCE_NAMES), "unique": True}
    if spec.field == "skill_include_groups":
        return {"item_type": "string_tuple", "item_min_items": 1}
    if spec.field == "private_placeholders":
        return {
            "value_format": {"regex": _ENV_NAME.pattern},
            "resolved_value_sensitive": True,
        }
    if spec.field == "prompt_dir":
        return {"requires": {"settings.prompt_revision": "nonempty"}}
    if spec.field == "output_mode":
        return {
            "requires_when": {
                "telegram": {"settings.output_cv_mode": "required"}
            }
        }
    return {}


def _metadata_value(value):
    if isinstance(value, tuple):
        return [_metadata_value(part) for part in value]
    if isinstance(value, dict):
        return {key: _metadata_value(part) for key, part in value.items()}
    return value


def validate_settings_path(path) -> LoadedSettings:
    """Validate one TOML file without reading environment overrides."""
    settings_path = _coerce_settings_path(path, Path.cwd())
    data = _parse_toml(settings_path)
    file_values = _validate_file_data(data)
    return _assemble(file_values, settings_path, environ={}, cwd=Path.cwd(), overrides=None)


def load_settings(environ=None, cwd=None, overrides=None) -> LoadedSettings:
    """Load settings using explicit values, environment, TOML, then defaults."""
    environ = os.environ if environ is None else environ
    cwd_path = Path.cwd() if cwd is None else Path(cwd)
    selected = str(environ.get("JOB_SEARCH_SETTINGS_FILE", "") or "").strip()
    settings_path = _coerce_settings_path(selected, cwd_path) if selected else None
    file_values = {}
    if settings_path is not None:
        file_values = _validate_file_data(_parse_toml(settings_path))
    return _assemble(file_values, settings_path, environ=environ, cwd=cwd_path, overrides=overrides)


def _coerce_settings_path(path, cwd: Path) -> Path:
    text = str(path or "").strip()
    if not text:
        raise ConfigValidationError("settings file path must be nonempty")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    if not candidate.is_file():
        raise ConfigValidationError("settings file does not exist: {}".format(candidate))
    return candidate.resolve()


def _parse_toml(path: Path) -> Mapping[str, Any]:
    toml = _toml_parser()
    try:
        with path.open("rb") as handle:
            parsed = toml.load(handle)
    except toml.TOMLDecodeError as exc:
        key = _duplicate_key_at_error(path, exc)
        if key:
            raise ConfigValidationError("duplicate setting: {}".format(key)) from exc
        raise ConfigValidationError("invalid settings TOML {}: {}".format(path, exc)) from exc
    except OSError as exc:
        raise ConfigValidationError("invalid settings TOML {}: {}".format(path, exc)) from exc
    if not isinstance(parsed, dict):
        raise ConfigValidationError("settings document must be a TOML table")
    return parsed


def _toml_parser():
    """Import a parser only when a caller actually selects a TOML file."""
    try:  # Python 3.11+
        import tomllib
        return tomllib
    except ModuleNotFoundError:
        try:  # Python 3.9-3.10 optional dependency
            import tomli
            return tomli
        except ModuleNotFoundError as exc:
            raise ConfigValidationError(
                "TOML settings require tomli on Python 3.9-3.10; install the optional dependency"
            ) from exc


def _duplicate_key_at_error(path: Path, error: Exception) -> str:
    """Add a dotted path to tomllib/tomli duplicate diagnostics."""
    message = str(error)
    lowered = message.lower()
    if not any(token in lowered for token in ("overwrite", "duplicate", "cannot declare")):
        return ""
    match = re.search(r"line (\d+)", message)
    if match is None:
        return ""
    section = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    line_number = int(match.group(1))
    for line in lines[:line_number - 1]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]").strip()
    offending = lines[line_number - 1].split("#", 1)[0].strip()
    if offending.startswith("[") and offending.endswith("]"):
        return offending.strip("[]").strip()
    key = offending.split("=", 1)[0].strip().strip('"')
    if not key:
        return ""
    inline_key = re.search(
        r"duplicate inline table key\s+['\"]([^'\"]+)['\"]", message, re.IGNORECASE
    )
    if inline_key is not None:
        key = "{}.{}".format(key, inline_key.group(1))
    return "{}.{}".format(section, key) if section else key


def _validate_file_data(data: Mapping[str, Any]) -> dict:
    unknown_sections = sorted(set(data) - _SECTIONS)
    if unknown_sections:
        raise ConfigValidationError("unknown settings section(s): {}".format(", ".join(unknown_sections)))
    if not data:
        raise ConfigValidationError("settings.version is required")
    settings = data.get("settings")
    if not isinstance(settings, dict) or "version" not in settings:
        raise ConfigValidationError("settings.version is required")
    values = {}
    for section, entries in data.items():
        if not isinstance(entries, dict):
            raise ConfigValidationError("{} must be a TOML table".format(section))
        for key, raw_value in entries.items():
            dotted = "{}.{}".format(section, key)
            spec = _BY_DOTTED.get(dotted)
            if spec is None:
                raise ConfigValidationError("unknown setting: {}".format(dotted))
            if spec.environment_only:
                raise ConfigValidationError("{} is environment-only".format(dotted))
            values[spec.field] = _coerce_value(spec, raw_value, dotted)
    if values.get("version") != SETTINGS_VERSION:
        raise ConfigValidationError(
            "settings.version must be {}".format(SETTINGS_VERSION)
        )
    return values


def _assemble(file_values, path, *, environ, cwd, overrides) -> LoadedSettings:
    values = {}
    origins = {}
    normalized_overrides = _normalize_overrides(overrides)
    for spec in OPTION_CATALOG:
        value = _copy_default(spec.default)
        origin = SettingOrigin("default", "default")
        if spec.field in file_values:
            value = file_values[spec.field]
            origin = SettingOrigin("file", spec.dotted_key)
        for env_name in spec.env:
            raw = environ.get(env_name)
            if raw is not None and str(raw).strip():
                value = _coerce_env_value(spec, raw, env_name)
                origin = SettingOrigin("env", env_name)
                break
        if spec.field in normalized_overrides:
            value = _coerce_value(spec, normalized_overrides[spec.field], spec.dotted_key)
            origin = SettingOrigin("explicit", spec.field)
        # Paths supplied by TOML are portable with the settings file for inputs
        # and deliberately write beneath the invocation directory for outputs.
        # Existing environment aliases retain their historic literal-path form.
        if spec.path_kind and value and origin.kind == "file":
            value = _resolve_path(value, spec.path_kind, origin, path, cwd)
        values[spec.field] = value
        origins[spec.field] = origin
    _validate_cross_fields(values, origins, selected_file=path is not None)
    return LoadedSettings(values, origins, path)


def _normalize_overrides(overrides) -> dict:
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise ConfigValidationError("explicit overrides must be a mapping")
    result = {}
    for key, value in overrides.items():
        text = str(key)
        spec = _BY_FIELD.get(text) or _BY_DOTTED.get(text)
        if spec is None:
            raise ConfigValidationError("unknown explicit setting: {}".format(text))
        if spec.environment_only:
            raise ConfigValidationError(
                "{} is environment-only and cannot be an explicit override".format(
                    spec.dotted_key
                )
            )
        result[spec.field] = value
    return result


def _copy_default(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, tuple):
        return tuple(value)
    return value


def _coerce_env_value(spec, raw, source):
    if spec.value_type in ("string_tuple", "country_tuple"):
        return _coerce_value(spec, [part.strip() for part in str(raw).split(",") if part.strip()], source)
    if spec.value_type == "bool":
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ConfigValidationError("{} must be a boolean".format(source))
    if spec.value_type in ("positive_int", "nonnegative_int", "office_days", "version"):
        try:
            raw = int(str(raw).strip())
        except ValueError:
            raise ConfigValidationError("{} must be an integer".format(source)) from None
    return _coerce_value(spec, raw, source)


def _coerce_value(spec: SettingSpec, value, source: str):
    kind = spec.value_type
    if kind in ("string", "version"):
        if kind == "version":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigValidationError("{} must be an integer".format(source))
            return value
        if not isinstance(value, str):
            raise ConfigValidationError("{} must be a string".format(source))
        result = value.strip()
        allowed = _ALLOWED_VALUES.get(spec.field)
        if allowed is not None and result.lower() not in allowed:
            raise ConfigValidationError("{} has unsupported value {!r}".format(source, result))
        return result
    if kind in ("positive_int", "nonnegative_int", "office_days"):
        if isinstance(value, bool):
            raise ConfigValidationError("{} must be an integer".format(source))
        if not isinstance(value, int):
            raise ConfigValidationError("{} must be an integer".format(source)) from None
        parsed = value
        minimum = 1 if kind == "positive_int" else 0
        maximum = 7 if kind == "office_days" else None
        if isinstance(value, float) or parsed < minimum or (maximum is not None and parsed > maximum):
            raise ConfigValidationError("{} has an invalid integer value".format(source))
        return parsed
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigValidationError("{} must be a boolean".format(source))
        return value
    if kind in ("string_tuple", "country_tuple", "check_order"):
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ConfigValidationError("{} must be an array of strings".format(source))
        result = tuple(item.strip() for item in value)
        if any(not item for item in result):
            raise ConfigValidationError("{} cannot contain empty strings".format(source))
        if kind == "country_tuple":
            result = tuple(_country_code(item, source) for item in result)
        allowed = _ALLOWED_VALUES.get(spec.field)
        if allowed is not None and any(item.lower() not in allowed for item in result):
            raise ConfigValidationError("{} has unsupported value".format(source))
        if kind == "check_order":
            unknown = set(result) - _CHECK_IDS
            if unknown or len(set(result)) != len(result):
                raise ConfigValidationError("{} has unknown or duplicate check identifier".format(source))
        if spec.field in ("sources_enable", "sources_disable", "employer_order") and len({item.lower() for item in result}) != len(result):
            raise ConfigValidationError("{} cannot contain duplicate identifiers".format(source))
        if spec.field in ("sources_enable", "sources_disable"):
            _validate_source_names(result, source)
        if spec.field == "forbidden_claim_patterns":
            for pattern in result:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigValidationError("{} has invalid regex {!r}: {}".format(source, pattern, exc)) from exc
        return result
    if kind == "string_tuple_tuple":
        if not isinstance(value, (list, tuple)):
            raise ConfigValidationError("{} must be an array of string arrays".format(source))
        groups = []
        for index, group in enumerate(value):
            item_source = "{}[{}]".format(source, index)
            parsed = _coerce_value(
                SettingSpec("", "", "", "string_tuple", (), ""), group, item_source
            )
            if not parsed:
                raise ConfigValidationError("{} cannot be empty".format(item_source))
            groups.append(parsed)
        return tuple(groups)
    if kind in ("string_map", "country_limit_map", "eu_region_limit_map"):
        if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
            raise ConfigValidationError("{} must be a table".format(source))
        result = {}
        for key, raw_limit in value.items():
            normalized = key.strip()
            if kind == "country_limit_map":
                normalized = _country_code(normalized, source)
            elif kind == "eu_region_limit_map":
                normalized = normalized.upper()
                if normalized != "EU":
                    raise ConfigValidationError("{} supports only EU".format(source))
            if not normalized:
                raise ConfigValidationError("{} cannot contain an empty key".format(source))
            if normalized in result:
                raise ConfigValidationError("{} cannot contain duplicate keys".format(source))
            if kind == "string_map":
                if not isinstance(raw_limit, str) or not raw_limit.strip():
                    raise ConfigValidationError("{} values must be nonempty strings".format(source))
                if spec.field == "private_placeholders" and not _ENV_NAME.fullmatch(raw_limit.strip()):
                    raise ConfigValidationError("{} values must be environment variable names".format(source))
                result[normalized] = raw_limit.strip()
            else:
                result[normalized] = _coerce_value(
                    SettingSpec("", "", "", "positive_int", 1, ""), raw_limit, source
                )
        return MappingProxyType(result)
    raise AssertionError("unsupported setting type: {}".format(kind))


def _country_code(value: str, source: str) -> str:
    country = value.upper()
    if not _COUNTRY_CODE.fullmatch(country) or country not in ISO_COUNTRY_CODES:
        raise ConfigValidationError("{} contains invalid ISO alpha-2 country code {!r}".format(source, value))
    return country


_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CHECK_IDS = {
    "language", "role_match", "excluded_industry", "excluded_platform_focus",
    "minimum_seniority", "local_office_attendance", "remote_location_residency",
    "nonremote_nonpermanent_employment", "remote_fact_residency",
    "nonremote_work_authorization", "nonremote_sponsorship", "nonremote_arrangement",
}
_KNOWN_SOURCE_NAMES = {
    "jobspy", "linkedin-global", "linkedin-israel", "linkedin-guest", "arc",
    "mobile.career", "jobscroller", "arbeitnow", "jobicy", "remoteok",
    "weworkremotely", "remotefirstjobs", "remotevibe", "himalayas", "remotive",
    "workingnomads", "themuse", "swissdevjobs", "relocate.me", "secrettelaviv",
}


def _validate_source_names(names, source):
    unknown = set(names) - _KNOWN_SOURCE_NAMES
    if unknown:
        raise ConfigValidationError("{} has unknown source(s): {}".format(source, ", ".join(sorted(unknown))))


def _resolve_path(value, path_kind, origin, settings_path, cwd) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate)
    if path_kind == "input" and origin.kind != "env" and settings_path is not None:
        return str((settings_path.parent / candidate).resolve())
    return str((Path(cwd) / candidate).resolve())


def _validate_cross_fields(values, origins, *, selected_file=False):
    if values["prompt_dir"] and not values["prompt_revision"]:
        raise ConfigValidationError(
            "settings.prompt_revision is required when settings.prompt_dir is set"
        )
    if values["output_mode"].lower() == "telegram" and values["output_cv_mode"].lower() != "required":
        raise ConfigValidationError(
            "settings.output_cv_mode must be required when settings.output_mode is telegram"
        )
    if selected_file:
        overlap = set(values["sources_enable"]) & set(values["sources_disable"])
        if overlap:
            raise ConfigValidationError(
                "search.sources_enable and search.sources_disable overlap: {}".format(
                    ", ".join(sorted(overlap))
                )
            )
