"""Load and validate the optional trusted Python composition module."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from .components import (
    CVCompiler,
    CVRenderer,
    CandidateFilter,
    CandidateProfile,
    Components,
    DefaultCVRenderer,
    DefaultJobEvaluator,
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
        previous = sys.modules.get(name)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
            raise
        return module
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "{} could not be loaded ({}: {})".format(path, type(exc).__name__, exc)
        ) from exc


def _require_protocol(name: str, value: object, protocol: object, problems: list) -> None:
    if not isinstance(value, protocol):
        problems.append("{} does not implement {}".format(name, protocol.__name__))


def validate_components(
    components: Components, settings: object, command: str = "daily", structural: bool = True
) -> None:
    problems = []
    if structural:
        _require_protocol("prompts", components.prompts, PromptSet, problems)
        _require_protocol("llm", components.llm, LLMService, problems)
        _require_protocol("candidate_filter", components.candidate_filter, CandidateFilter, problems)
        _require_protocol("evaluator", components.evaluator, JobEvaluator, problems)
        if not isinstance(components.profile, CandidateProfile):
            problems.append("profile must be a CandidateProfile")
        _require_protocol("cv_renderer", components.cv_renderer, CVRenderer, problems)
        _require_protocol("section_provider", components.section_provider, SectionProvider, problems)
        _require_protocol("output_renderer", components.output_renderer, OutputRenderer, problems)
        _require_protocol("output_backend", components.output_backend, OutputBackend, problems)

    profile = getattr(components, "profile", None)
    if isinstance(profile, CandidateProfile):
        for label, value in (
            ("profile.display_name", profile.display_name),
            ("profile.base_tex_path", profile.base_tex_path),
            ("profile.rendered_base_path", profile.rendered_base_path),
            ("profile.cv_filename_prefix", profile.cv_filename_prefix),
            ("profile.revision", profile.revision),
        ):
            if not isinstance(value, str) or not value.strip():
                problems.append("{} must be a nonempty string".format(label))

        employer_order = profile.employer_order
        if (
            not isinstance(employer_order, Sequence)
            or isinstance(employer_order, (str, bytes))
            or not employer_order
            or any(
                not isinstance(employer, str) or not employer.strip()
                for employer in employer_order
            )
        ):
            problems.append(
                "profile.employer_order must be a nonempty sequence of nonempty strings"
            )

        patterns = profile.forbidden_claim_patterns
        if not isinstance(patterns, Sequence) or isinstance(patterns, (str, bytes)):
            problems.append(
                "profile.forbidden_claim_patterns must be a sequence of strings"
            )
        else:
            for pattern in patterns:
                if not isinstance(pattern, str):
                    problems.append(
                        "profile.forbidden_claim_patterns must contain only strings"
                    )
                    continue
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(
                        "invalid profile forbidden-claim pattern: {}".format(exc)
                    )

        placeholders = profile.private_placeholders
        if not isinstance(placeholders, Mapping):
            problems.append("profile.private_placeholders must be a string mapping")
        else:
            for placeholder, env_name in placeholders.items():
                if (
                    not isinstance(placeholder, str)
                    or not placeholder
                    or not isinstance(env_name, str)
                    or not env_name.strip()
                ):
                    problems.append(
                        "profile.private_placeholders must map nonempty strings"
                    )
    renderer = getattr(components, "cv_renderer", None)
    if type(renderer) is DefaultCVRenderer:
        if not isinstance(renderer.compiler, CVCompiler):
            problems.append("default CV renderer compiler does not implement CVCompiler")
        elif not str(getattr(renderer.compiler, "executable", "") or "").strip():
            problems.append("default CV renderer compiler executable must be nonempty")

    auth_modes = {
        "llm_primary_auth_mode": getattr(settings, "llm_primary_auth_mode", "bearer"),
        "llm_fallback_auth_mode": getattr(settings, "llm_fallback_auth_mode", "bearer"),
    }
    for name, value in auth_modes.items():
        if value not in ("bearer", "none"):
            problems.append("{} must be 'bearer' or 'none'".format(name))
    primary = getattr(components.llm, "primary", None)
    fallback = getattr(components.llm, "fallback", None)
    if auth_modes["llm_primary_auth_mode"] == "none" and primary is not None:
        if getattr(primary, "scheme", "") != "openai":
            problems.append("llm_primary_auth_mode='none' requires the openai scheme")
    if auth_modes["llm_fallback_auth_mode"] == "none" and fallback is not None:
        if getattr(fallback, "scheme", "") != "openai":
            problems.append("llm_fallback_auth_mode='none' requires the openai scheme")

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

    if command in ("daily", "tailor"):
        if getattr(components.llm, "requires_api_key", False) and not getattr(
            settings, "llm_primary_api_key", ""
        ):
            problems.append("the configured primary LLM requires an API key")
        if getattr(components.output_backend, "requires_telegram_credentials", False):
            if not getattr(settings, "telegram_bot_token", "") or not getattr(
                settings, "telegram_chat_id", ""
            ):
                problems.append(
                    "the configured Telegram backend requires TELEGRAM_BOT_TOKEN "
                    "and TELEGRAM_CHAT_ID"
                )

    # Command-aware local-file preflight is deliberately read-only. Sections
    # remain a soft presentation fallback and seen-state may be absent on a
    # first run, so neither belongs here.
    required_files = []
    if command in ("daily", "check"):
        required_files.append(
            ("criteria_file", getattr(settings, "criteria_file", "criteria.md"))
        )
    renderer_profile = getattr(
        getattr(components, "cv_renderer", None), "profile", None
    )
    if (
        command in ("daily", "tailor", "base", "check")
        and cv_mode == "required"
        and type(getattr(components, "cv_renderer", None)) is DefaultCVRenderer
        and isinstance(renderer_profile, CandidateProfile)
    ):
        required_files.append(("base_tex_path", renderer_profile.base_tex_path))
        if command in ("daily", "tailor", "check"):
            required_files.append(
                (
                    "cv_tailoring_prompt_file",
                    getattr(
                        settings,
                        "cv_tailoring_prompt_file",
                        "cv_tailoring_prompt.md",
                    ),
                )
            )
    for label, path in required_files:
        try:
            file_path = os.fspath(path)
            valid_file = bool(str(file_path or "").strip()) and os.path.isfile(
                file_path
            )
        except TypeError:
            valid_file = False
        if not valid_file:
            problems.append("{} does not name a readable file: {}".format(label, path))

    if problems:
        raise ConfigurationError("Invalid job-search configuration: " + "; ".join(problems))


def load_components(
    settings: object, command: str = "daily", defaults: Components = None,
    validate_defaults: bool = True,
) -> Components:
    """Return validated defaults overlaid by ``configure`` when present.

    The default filename is optional. Setting ``JOB_SEARCH_CONFIG_FILE`` makes
    the path explicit, so a missing file is an error rather than a silent
    fallback. The module is trusted executable Python and is loaded directly by
    path; it may import separately installed packages in the normal way.
    """
    path, explicit = _config_path()
    defaults = defaults or default_components(settings)
    if not os.path.exists(path):
        if explicit:
            raise ConfigurationError("Configured composition file does not exist: {}".format(path))
        validate_components(defaults, settings, command, structural=validate_defaults)
        defaults._customized = False
        return defaults
    if not os.path.isfile(path):
        raise ConfigurationError("Configured composition path is not a file: {}".format(path))

    module = _load_module(path)
    configure = getattr(module, "configure", None)
    if not callable(configure):
        raise ConfigurationError("{} must export configure(defaults, settings)".format(path))
    baseline = {
        name: getattr(defaults, name) for name in Components.__dataclass_fields__
    }
    try:
        configured = configure(defaults, settings)
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "{} configure() failed ({}: {})".format(path, type(exc).__name__, exc)
        ) from exc
    if not isinstance(configured, Components):
        raise ConfigurationError("{} configure() must return Components".format(path))
    if (
        configured.evaluator is defaults.evaluator
        and configured.prompts is not defaults.prompts
        and isinstance(defaults.evaluator, DefaultJobEvaluator)
    ):
        configured.evaluator = type(defaults.evaluator)(configured.prompts)
    if (
        configured.cv_renderer is defaults.cv_renderer
        and type(defaults.cv_renderer) is DefaultCVRenderer
        and (
            configured.profile is not defaults.profile
            or configured.prompts is not defaults.prompts
        )
    ):
        configured.cv_renderer = type(defaults.cv_renderer)(
            defaults.cv_renderer.settings,
            configured.profile,
            prompts=configured.prompts,
        )
    validate_components(configured, settings, command)
    configured._customized = any(
        getattr(configured, name) is not value
        for name, value in baseline.items()
    )
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
