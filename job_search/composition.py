"""Load and validate the optional trusted Python composition module."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, replace

from .components import (
    CandidateProfile,
    Components,
    DefaultCVRenderer,
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


def _require_shape(name: str, value: object, attributes: tuple, problems: list) -> None:
    """Check that ``value`` exposes each of ``attributes`` by name.

    This is what ``isinstance`` against a ``runtime_checkable`` Protocol used
    to do here: check that the named *methods and attributes exist*, never
    their signatures or return types. This catches a typo'd method name and a
    missing attribute; it does not prove a component behaves.
    """
    missing = [attribute for attribute in attributes if not hasattr(value, attribute)]
    if missing:
        problems.append(
            "{} is missing required attribute(s): {}".format(name, ", ".join(missing))
        )


def _validate_shape(components: Components, problems: list) -> None:
    """Check the object graph against the component contracts."""
    _require_shape(
        "prompts", components.prompts,
        ("fact_extraction", "job_summary", "cv_bullet_selection", "compiler_repair"),
        problems,
    )
    _require_shape("llm", components.llm, ("generate", "usage_summary"), problems)
    if components.candidate_filter is not None and not callable(components.candidate_filter):
        problems.append("candidate_filter must be None or a callable(job) -> bool")
    if not isinstance(components.profile, CandidateProfile):
        problems.append("profile must be a CandidateProfile")
    _require_shape(
        "cv_renderer", components.cv_renderer,
        ("media_types", "render_tailored", "render_base"),
        problems,
    )
    # output_renderer / output_backend are no longer checked against a shared
    # protocol: OUTPUT_MODE chooses the built-in pair as a unit, so the two
    # can no longer disagree, and a hatch-supplied pair is the one place this
    # refactor leaves deliberately unvalidated (see module docstring).

    # Duck-typed rather than keyed on DefaultCVRenderer: any renderer that
    # exposes a compiler is making the same promise about it, and a validator
    # that names concrete classes is a sign the contract is not carrying its
    # weight.
    compiler = getattr(getattr(components, "cv_renderer", None), "compiler", None)
    if compiler is not None:
        if not hasattr(compiler, "compile"):
            problems.append("CV renderer compiler is missing required attribute(s): compile")
        elif not str(getattr(compiler, "executable", "") or "").strip():
            problems.append("CV renderer compiler executable must be nonempty")


def _validate_environment(
    components: Components, settings: object, command: str, problems: list
) -> None:
    """Check this host: credentials, auth modes, and the files the command needs.

    Separated from the shape checks because these are the ones that fail on a
    real runner at 7am — a missing key, an unreadable criteria file — while the
    shape checks fail the moment a configuration is written.
    """
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

    # OUTPUT_MODE/OUTPUT_CV_MODE are settings, not object attributes: the
    # built-in output_backend no longer carries a cv_mode of its own (see
    # FilesystemOutputBackend.require_artifact), and a hatch-supplied backend
    # is trusted to match whatever it was configured to match.
    output_mode = getattr(settings, "output_mode", "telegram")
    cv_mode = getattr(settings, "output_cv_mode", "required")
    # TODO(C10): move into runtime.preflight, keyed on rt.cv_required /
    # rt.needs_telegram, once those exist — see runtime.py design notes.
    if output_mode == "telegram" and cv_mode != "required":
        problems.append("OUTPUT_MODE=telegram requires OUTPUT_CV_MODE=required")

    # PROMPT_REVISION is required whenever PROMPT_DIR is set: prompt wording
    # participates in evaluation reopening (criteria_fingerprint), so a
    # missing revision would silently reuse the wrong reopen fingerprint
    # across a prompt change rather than fail loudly here.
    # TODO(C10): move into runtime.preflight alongside the OUTPUT_MODE check
    # above, once it exists — see runtime.py design notes.
    if getattr(settings, "prompt_dir", "") and not getattr(settings, "prompt_revision", ""):
        problems.append("PROMPT_DIR is set but PROMPT_REVISION is empty")

    if command in ("daily", "tailor"):
        if getattr(components.llm, "requires_api_key", False) and not getattr(
            settings, "llm_primary_api_key", ""
        ):
            problems.append("the configured primary LLM requires an API key")
        if output_mode == "telegram":
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
    renderer = getattr(components, "cv_renderer", None)
    renderer_profile = getattr(renderer, "profile", None)
    if (
        command in ("daily", "tailor", "base", "check")
        and cv_mode == "required"
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


def validate_components(
    components: Components, settings: object, command: str = "daily"
) -> None:
    """Raise ConfigurationError describing everything wrong, before side effects."""
    problems = []
    _validate_shape(components, problems)
    _validate_environment(components, settings, command, problems)
    if problems:
        raise ConfigurationError("Invalid job-search configuration: " + "; ".join(problems))


def rebind_defaults(baseline: Mapping, configured: Components) -> Components:
    """Re-wire built-in components a configuration left alone but invalidated.

    Replacing only ``prompts`` (or only ``profile``) is the common override, and
    the built-in CV renderer was constructed against the old ones. A component
    the configuration replaced outright is never touched — that object is the
    author's, and its wiring is their business.
    """
    changes = {}
    renderer = configured.cv_renderer
    if (
        renderer is baseline["cv_renderer"]
        and isinstance(renderer, DefaultCVRenderer)
        and (
            configured.profile is not baseline["profile"]
            or configured.prompts is not baseline["prompts"]
        )
    ):
        changes["cv_renderer"] = type(renderer)(
            renderer.settings, configured.profile, prompts=configured.prompts
        )
    return replace(configured, **changes) if changes else configured


def load_components(
    settings: object, command: str = "daily", defaults: Components = None,
    environ: Mapping = None,
) -> Components:
    """Return validated defaults overlaid by ``configure`` when present.

    The default filename is optional. Setting ``JOB_SEARCH_CONFIG_FILE`` makes
    the path explicit, so a missing file is an error rather than a silent
    fallback.

    The module is **trusted executable Python**, loaded by path and run at
    import: its mere presence in the working directory is enough to execute it,
    with no opt-in flag. It may import separately installed packages in the
    normal way. Treat adding or editing one as you would a CI workflow change.

    ``environ`` is injected like ``settings`` rather than read from the process,
    so a caller can resolve the path against something other than os.environ.
    """
    path, explicit = _config_path(environ)
    if defaults is None:
        try:
            defaults = default_components(settings)
        except ConfigurationError:
            raise
        except (Exception, SystemExit) as exc:
            raise ConfigurationError(
                "Built-in components could not be constructed ({}: {})".format(
                    type(exc).__name__, exc
                )
            ) from exc
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
    configured = rebind_defaults(baseline, configured)
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
    # output_mode / output_cv_mode already surface as top-level settings
    # (asdict(settings) above) — the pair is chosen by OUTPUT_MODE now, so
    # there is nothing left on the components themselves worth annotating.
    return json.dumps(values, indent=2, sort_keys=True)


__all__ = [
    "Components", "ConfigurationError", "load_components",
    "rebind_defaults", "redacted_configuration", "validate_components",
]
