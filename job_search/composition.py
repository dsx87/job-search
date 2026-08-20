"""Load and validate the optional trusted Python composition module."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import asdict

from .components import (
    CVRenderer,
    CandidateFilter,
    Components,
    JobEvaluator,
    LLMService,
    OutputBackend,
    OutputRenderer,
    PromptSet,
    SectionProvider,
    default_components,
)

DEFAULT_CONFIG_FILE = "job_search_config.py"
CONFIG_FILE_ENV = "JOB_SEARCH_CONFIG_FILE"


class ConfigurationError(ValueError):
    """Raised before pipeline side effects when composition is unusable."""


def _config_path(environ=None):
    environ = os.environ if environ is None else environ
    explicit = CONFIG_FILE_ENV in environ
    raw = str(environ.get(CONFIG_FILE_ENV, "") if explicit else DEFAULT_CONFIG_FILE).strip()
    if explicit and not raw:
        raise ConfigurationError("JOB_SEARCH_CONFIG_FILE is set but empty")
    return raw or DEFAULT_CONFIG_FILE, explicit


def _load_module(path: str):
    absolute = os.path.abspath(path)
    token = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:12]
    name = "job_search_user_config_{}".format(token)
    try:
        spec = importlib.util.spec_from_file_location(name, absolute)
        if spec is None or spec.loader is None:
            raise ImportError("not an importable Python source file")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "{} could not be loaded ({}: {})".format(path, type(exc).__name__, exc)
        ) from exc


def _require_protocol(name: str, value: object, protocol: object, problems: list) -> None:
    if not isinstance(value, protocol):
        problems.append("{} does not implement {}".format(name, protocol.__name__))


def validate_components(components: Components, settings: object, command: str = "daily") -> None:
    problems = []
    _require_protocol("prompts", components.prompts, PromptSet, problems)
    _require_protocol("llm", components.llm, LLMService, problems)
    _require_protocol("candidate_filter", components.candidate_filter, CandidateFilter, problems)
    _require_protocol("evaluator", components.evaluator, JobEvaluator, problems)
    _require_protocol("cv_renderer", components.cv_renderer, CVRenderer, problems)
    _require_protocol("section_provider", components.section_provider, SectionProvider, problems)
    _require_protocol("output_renderer", components.output_renderer, OutputRenderer, problems)
    _require_protocol("output_backend", components.output_backend, OutputBackend, problems)

    auth_modes = {
        "llm_primary_auth_mode": getattr(settings, "llm_primary_auth_mode", "bearer"),
        "llm_fallback_auth_mode": getattr(settings, "llm_fallback_auth_mode", "bearer"),
    }
    for name, value in auth_modes.items():
        if value not in ("bearer", "none"):
            problems.append("{} must be 'bearer' or 'none'".format(name))

    renderer_kind = getattr(components.output_renderer, "kind", "")
    accepted_kinds = tuple(getattr(components.output_backend, "accepted_renderer_kinds", ()))
    if renderer_kind not in accepted_kinds:
        problems.append(
            "output renderer kind {!r} is not accepted by backend ({})".format(
                renderer_kind, ", ".join(accepted_kinds) or "none"
            )
        )

    cv_mode = getattr(components.output_backend, "cv_mode", "")
    if cv_mode not in ("required", "disabled"):
        problems.append("output backend cv_mode must be 'required' or 'disabled'")
    if command == "tailor" and cv_mode != "required":
        problems.append("--tailor requires a CV-capable output backend")
    if cv_mode == "required":
        produced = set(getattr(components.cv_renderer, "media_types", ()))
        accepted = set(getattr(components.output_backend, "accepted_media_types", ()))
        if not produced.intersection(accepted):
            problems.append(
                "CV artifact media types ({}) are not accepted by the output backend ({})".format(
                    ", ".join(sorted(produced)) or "none",
                    ", ".join(sorted(accepted)) or "none",
                )
            )

    if problems:
        raise ConfigurationError("Invalid job-search configuration: " + "; ".join(problems))


def load_components(settings: object, command: str = "daily") -> Components:
    """Return validated defaults overlaid by ``configure`` when present.

    The default filename is optional. Setting ``JOB_SEARCH_CONFIG_FILE`` makes
    the path explicit, so a missing file is an error rather than a silent
    fallback. The module is trusted executable Python and is loaded directly by
    path; it may import separately installed packages in the normal way.
    """
    path, explicit = _config_path()
    defaults = default_components(settings)
    if not os.path.exists(path):
        if explicit:
            raise ConfigurationError("Configured composition file does not exist: {}".format(path))
        validate_components(defaults, settings, command)
        return defaults
    if not os.path.isfile(path):
        raise ConfigurationError("Configured composition path is not a file: {}".format(path))

    module = _load_module(path)
    configure = getattr(module, "configure", None)
    if not callable(configure):
        raise ConfigurationError("{} must export configure(defaults, settings)".format(path))
    try:
        configured = configure(defaults, settings)
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "{} configure() failed ({}: {})".format(path, type(exc).__name__, exc)
        ) from exc
    if not isinstance(configured, Components):
        raise ConfigurationError("{} configure() must return Components".format(path))
    validate_components(configured, settings, command)
    return configured


def redacted_configuration(settings: object, components: Components) -> str:
    """Stable JSON suitable for ``--check-config`` output."""
    values = asdict(settings)
    for name in tuple(values):
        if any(secret in name for secret in ("api_key", "token", "chat_id")):
            values[name] = "<redacted>" if values[name] else "<unset>"
    values["components"] = {
        name: type(getattr(components, name)).__name__
        for name in Components.__dataclass_fields__
    }
    values["components"]["output_renderer"] += " ({})".format(
        getattr(components.output_renderer, "kind", "unknown")
    )
    values["components"]["output_backend"] += " (cv_mode={})".format(
        getattr(components.output_backend, "cv_mode", "unknown")
    )
    return json.dumps(values, indent=2, sort_keys=True)


__all__ = [
    "Components", "ConfigurationError", "load_components",
    "redacted_configuration", "validate_components",
]
