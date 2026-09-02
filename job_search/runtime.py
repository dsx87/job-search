"""Build the pipeline's object graph from settings, with one escape hatch.

Replaces ``composition.py``: there is no Protocol layer and nothing here
validates a hatch-supplied object's shape. ``build_runtime`` constructs the
built-in graph from settings, lets the trusted ``job_search_config.py`` module
mutate or replace it (``apply_user_config``), and then checks this host is
actually usable for ``command`` (``preflight``) — reading the resulting
``Runtime``'s derived flags, not settings directly, so a hatch that swaps in a
CV-less, non-Telegram backend is never asked for credentials it will never
use.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import sys
from dataclasses import asdict, dataclass

from .components import CandidateProfile, DefaultCVRenderer, _default_output_pair, _default_prompts
from .config import (
    BASE_TEX_FILE,
    ConfigurationError,
    CRITERIA_FILE,
    CV_TAILORING_PROMPT_FILE,
)

DEFAULT_CONFIG_FILE = "job_search_config.py"
CONFIG_FILE_ENV = "JOB_SEARCH_CONFIG_FILE"


@dataclass
class Runtime:
    """The pipeline's object graph: five collaborators, four derived flags.

    Deliberately mutable: the escape hatch may either return
    ``dataclasses.replace(runtime, ...)`` or assign fields in place (the
    common idiom is ``return configure(...) or runtime``).
    """

    llm: object                     # LLMClient — holds breaker state, token totals, lock
    prompts: object                 # DefaultPromptSet or FilePromptSet
    cv_renderer: object             # bundles settings + profile + prompts + LatexCompiler
    renderer: object                # output renderer
    backend: object                 # output backend
    candidate_filter: object = None  # optional callable(job) -> bool
    # Derived from settings in build_runtime; the escape hatch may flip these
    # so a swapped-in backend/renderer stays coherent with the pipeline's own
    # branching (PipelineConfig is frozen, so settings themselves cannot).
    cv_required: bool = True        # was backend.cv_mode == "required"
    needs_telegram: bool = True     # was backend.requires_telegram_credentials
    needs_base_tex: bool = True     # was reads_profile_sources(cv_renderer)
    telegram_markup: bool = True    # was output_renderer.kind == "telegram"
    config_file: str = ""           # which escape hatch ran, surfaced by --check-config


def _config_path(environ=None):
    environ = os.environ if environ is None else environ
    explicit = CONFIG_FILE_ENV in environ
    raw = str(environ.get(CONFIG_FILE_ENV, "") if explicit else DEFAULT_CONFIG_FILE).strip()
    if explicit and not raw:
        raise ConfigurationError("JOB_SEARCH_CONFIG_FILE is set but empty")
    return raw or DEFAULT_CONFIG_FILE, explicit


def _load_module(path: str):
    """Import ``path`` as a fresh module and return it.

    Errors raised while the module's top-level code runs propagate raw — a
    real traceback beats a mangled string. ``sys.modules`` pre-registration
    happens before ``exec_module`` because a hatch file that defines a
    ``@dataclass`` needs its defining module discoverable while the class
    body executes.
    """
    absolute = os.path.abspath(path)
    token = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:12]
    name = "job_search_user_config_{}".format(token)
    spec = importlib.util.spec_from_file_location(name, absolute)
    if spec is None or spec.loader is None:
        raise ImportError("{} is not an importable Python source file".format(path))
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


def apply_user_config(runtime: Runtime, settings: object, *, environ=None) -> Runtime:
    """Run the trusted escape hatch, if one is configured, and return the result.

    The default filename is optional. Setting ``JOB_SEARCH_CONFIG_FILE`` makes
    the path explicit, so a missing file is an error rather than a silent
    fallback — including an explicitly-set-but-empty value, so a blank line
    in a deployed ``.env`` can't silently bypass a local config.

    The module is **trusted executable Python**, loaded by path and run at
    import: its mere presence in the working directory is enough to execute
    it, with no opt-in flag.

    We raise ``ConfigurationError`` ourselves in exactly two cases beyond the
    missing-file one above: a module with no ``configure``, and an old-style
    ``configure(defaults, settings)`` module (detected by the first parameter
    being named ``defaults``), which gets a one-line migration message rather
    than an opaque ``TypeError``. Anything ``configure()`` itself raises
    propagates unwrapped.
    """
    path, explicit = _config_path(environ)
    if not os.path.exists(path):
        if explicit:
            raise ConfigurationError(
                "Configured job_search_config file does not exist: {}".format(path)
            )
        return runtime
    if not os.path.isfile(path):
        raise ConfigurationError("Configured job_search_config path is not a file: {}".format(path))

    module = _load_module(path)
    configure = getattr(module, "configure", None)
    if not callable(configure):
        raise ConfigurationError("{} must export configure(runtime, settings)".format(path))
    parameters = list(inspect.signature(configure).parameters)
    if parameters and parameters[0] == "defaults":
        raise ConfigurationError(
            "{} uses the old configure(defaults, settings) signature; migrate to "
            "configure(runtime, settings) — mutate the runtime in place and/or "
            "return it.".format(path)
        )
    runtime = configure(runtime, settings) or runtime
    runtime.config_file = path
    return runtime


def preflight(settings: object, runtime: Runtime, command: str = "daily") -> None:
    """Check this host: settings combinations, credentials, and required files.

    Reads ``runtime``'s derived flags rather than settings directly, so a
    hatch that swapped in a CV-less, non-Telegram pair is never asked for
    Telegram credentials or CV source files it will never touch. Command-aware
    local-file preflight is deliberately read-only: sections remain a soft
    presentation fallback and seen-state may be absent on a first run, so
    neither belongs here.
    """
    problems = []

    output_mode = getattr(settings, "output_mode", "telegram")
    cv_mode = getattr(settings, "output_cv_mode", "required")
    if output_mode == "telegram" and cv_mode != "required":
        problems.append("OUTPUT_MODE=telegram requires OUTPUT_CV_MODE=required")

    # PROMPT_REVISION is required whenever PROMPT_DIR is set: prompt wording
    # participates in evaluation reopening (criteria_fingerprint), so a
    # missing revision would silently reuse the wrong reopen fingerprint
    # across a prompt change rather than fail loudly here.
    if getattr(settings, "prompt_dir", "") and not getattr(settings, "prompt_revision", ""):
        problems.append("PROMPT_DIR is set but PROMPT_REVISION is empty")

    if command in ("daily", "tailor"):
        if getattr(runtime.llm, "requires_api_key", False) and not getattr(
            settings, "llm_primary_api_key", ""
        ):
            problems.append("the configured primary LLM requires an API key")
        if getattr(runtime, "needs_telegram", True):
            if not getattr(settings, "telegram_bot_token", "") or not getattr(
                settings, "telegram_chat_id", ""
            ):
                problems.append(
                    "the configured Telegram backend requires TELEGRAM_BOT_TOKEN "
                    "and TELEGRAM_CHAT_ID"
                )

    required_files = []
    if command in ("daily", "check"):
        required_files.append(("criteria_file", getattr(settings, "criteria_file", CRITERIA_FILE)))
    if command in ("daily", "tailor", "base", "check") and getattr(runtime, "needs_base_tex", True):
        required_files.append(("base_tex_path", getattr(settings, "base_tex_file", BASE_TEX_FILE)))
        if command in ("daily", "tailor", "check"):
            required_files.append(
                (
                    "cv_tailoring_prompt_file",
                    getattr(settings, "cv_tailoring_prompt_file", CV_TAILORING_PROMPT_FILE),
                )
            )
    for label, path in required_files:
        try:
            file_path = os.fspath(path)
            valid_file = bool(str(file_path or "").strip()) and os.path.isfile(file_path)
        except TypeError:
            valid_file = False
        if not valid_file:
            problems.append("{} does not name a readable file: {}".format(label, path))

    if problems:
        raise ConfigurationError("Invalid job-search configuration: " + "; ".join(problems))


def build_runtime(
    settings: object, command: str = "daily", *, llm: object = None,
    telegram: object = None, environ=None,
) -> Runtime:
    """Build the built-in object graph, apply the escape hatch, then preflight it.

    ``llm`` / ``telegram`` let a caller (``pipeline.run``) construct those two
    stateful collaborators itself and hand them in, matching how they were
    already built once per run before this function existed.
    """
    from .llm.clients import LLMClient

    try:
        prompts = _default_prompts(settings)
        llm = llm or LLMClient.from_config(settings)
        profile = CandidateProfile.from_settings(settings)
        cv_renderer = DefaultCVRenderer(settings, profile, prompts=prompts)
        renderer, backend = _default_output_pair(settings, telegram)
    except ConfigurationError:
        raise
    except (Exception, SystemExit) as exc:
        raise ConfigurationError(
            "Built-in components could not be constructed ({}: {})".format(
                type(exc).__name__, exc
            )
        ) from exc

    cv_required = getattr(settings, "output_cv_mode", "required") == "required"
    output_mode = getattr(settings, "output_mode", "telegram")
    runtime = Runtime(
        llm=llm,
        prompts=prompts,
        cv_renderer=cv_renderer,
        renderer=renderer,
        backend=backend,
        cv_required=cv_required,
        needs_telegram=output_mode == "telegram",
        needs_base_tex=cv_required,
        telegram_markup=output_mode == "telegram",
    )
    runtime = apply_user_config(runtime, settings, environ=environ)
    preflight(settings, runtime, command)
    return runtime


def redacted_settings(settings: object, runtime: Runtime) -> str:
    """Stable JSON suitable for ``--check-config`` output."""
    values = asdict(settings)
    for name in tuple(values):
        if any(secret in name for secret in ("api_key", "token", "chat_id")):
            values[name] = "<redacted>" if values[name] else "<unset>"
    values["runtime"] = {
        "llm": type(runtime.llm).__name__,
        "prompts": type(runtime.prompts).__name__,
        "cv_renderer": type(runtime.cv_renderer).__name__,
        "renderer": type(runtime.renderer).__name__,
        "backend": type(runtime.backend).__name__,
        "candidate_filter": type(runtime.candidate_filter).__name__,
        "cv_required": runtime.cv_required,
        "needs_telegram": runtime.needs_telegram,
        "needs_base_tex": runtime.needs_base_tex,
        "telegram_markup": runtime.telegram_markup,
        "config_file": runtime.config_file or None,
    }
    return json.dumps(values, indent=2, sort_keys=True)


__all__ = [
    "CONFIG_FILE_ENV",
    "ConfigurationError",
    "DEFAULT_CONFIG_FILE",
    "Runtime",
    "apply_user_config",
    "build_runtime",
    "preflight",
    "redacted_settings",
]
