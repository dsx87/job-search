"""Convert the engine's setup into the Job Hunter Desktop engine-import format.

    python scripts/convert_engine.py --config job_search.toml --out engine-import.json \
        [--criteria criteria.md] [--tex base.tex] [--seen state/seen_jobs.json] [--state job_state.json]

The output is {"format": "jobhunter-engine-import", "version": 1, ...}: the effective values of the
settings the app maps (engine defaults applied), the effective source list, the criteria and CV
texts, the history (the jobhunter-history object of convert_history.py), the jobs of job_state.json
and the sorted paths of every setting the app does not take, without their values. It never holds a
credential: credentials are environment-only in the engine and the script reads no environment.
The format is described in the app repo's docs/engine-import.md. Only counts are printed.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_history import convert as convert_history  # noqa: E402
from job_search import settings as engine_settings  # noqa: E402
from job_search.sources.fetch import select_sources  # noqa: E402

FORMAT = "jobhunter-engine-import"
VERSION = 1

# The settings the app maps, by section. Everything else in the TOML is listed as unsupported.
MAPPED = {
    "settings": (
        "llm_primary_scheme", "llm_primary_model", "llm_primary_api_base", "llm_primary_auth_mode",
        "llm_fallback_scheme", "llm_fallback_model", "llm_fallback_api_base", "llm_fallback_auth_mode",
        "scrape_budget_seconds",
    ),
    "search": (
        "role_include_terms", "role_exclude_terms", "skill_include_groups", "location_exclude_terms",
        "relocation_regions", "max_age_days", "remote_allowed", "relocation_allowed", "search_terms",
        "query_locations", "results_per_query",
    ),
    "candidate": (
        "max_pages", "max_pages_by_country", "max_pages_by_region", "display_name",
        "residency_countries", "work_authorization_countries",
    ),
    "policy": (
        "check_order", "require_english", "local_language_exempt", "excluded_industries",
        "excluded_platform_focuses", "rejected_seniority", "max_local_office_days",
        "allow_sponsorship_override", "allowed_languages", "preferred_working_hours",
    ),
}
# Keys the script reads itself: they become `sources`, `criteria` and `tex`, not values.
CONSUMED = {"settings.version", "settings.criteria_file", "candidate.base_tex_file",
            "search.sources_enable", "search.sources_disable"}
# Sources the app does not have (spec D6).
UNSUPPORTED_SOURCES = ("jobspy", "linkedin-global", "linkedin-israel", "linkedin-guest", "secrettelaviv")
JOB_FIELDS = ("title", "company", "location", "url", "source", "date_posted", "is_remote")


class ConvertError(Exception):
    pass


def _plain(value):
    """Tuples and read-only mappings as JSON lists and objects."""
    if isinstance(value, (list, tuple)):
        return [_plain(part) for part in value]
    if hasattr(value, "items"):
        return {str(key): _plain(part) for key, part in sorted(value.items())}
    return value


def _read_toml(path: Path) -> dict:
    import tomllib
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConvertError(f"cannot read {path}: {type(error).__name__}") from error


def _split(raw: dict):
    """Known file keys for the engine's loader, and the sorted paths it does not know."""
    known, unknown = {}, []
    for section, entries in raw.items():
        if not isinstance(entries, dict):
            unknown.append(section)
            continue
        for key in entries:
            dotted = f"{section}.{key}"
            spec = engine_settings._BY_DOTTED.get(dotted)
            if spec is None or spec.environment_only:
                unknown.append(dotted)
            else:
                known.setdefault(section, {})[key] = entries[key]
    return known, unknown


def effective_settings(config: Path):
    """The engine's effective values for the file's recognized keys, plus the unknown paths.

    The engine's loader rejects unknown keys and reads only whole files, so this calls its two
    inner steps (validation and assembly) on the recognized keys with an empty environment:
    defaults and type checks stay the engine's own."""
    raw = _read_toml(config)
    known, unknown = _split(raw)
    try:
        file_values = engine_settings._validate_file_data(known)
        loaded = engine_settings._assemble(file_values, config, environ={}, cwd=config.parent, overrides=None)
    except engine_settings.ConfigValidationError as error:
        raise ConvertError(f"invalid settings: {error}") from error
    present = {f"{section}.{key}" for section, entries in known.items() for key in entries}
    return loaded.values, present, unknown


def _beside(config: Path, value: str):
    """A path setting relative to the TOML's folder. The loader resolves TOML values that way but
    leaves defaults relative to the working directory."""
    return (config.parent / value).resolve() if value else None


def _read_text(path, required: bool):
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        if required:
            raise ConvertError(f"file not found: {path}")
        return None
    return path.read_text(encoding="utf-8")


def _read_json(path: Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConvertError(f"cannot read {what} {path}: {type(error).__name__}") from error


def convert_jobs(state) -> list:
    if not isinstance(state, dict) or not isinstance(state.get("jobs", {}), dict):
        raise ConvertError("job state is not an object with a 'jobs' object")
    jobs = []
    for entry in state.get("jobs", {}).values():
        if not isinstance(entry, dict):
            continue
        job = {field: entry.get(field) for field in JOB_FIELDS}
        job["seen"] = bool(entry.get("seen", False))
        jobs.append(job)
    return sorted(jobs, key=lambda job: (str(job["url"] or ""), str(job["title"] or ""), str(job["company"] or "")))


def _check_endpoints(mapped: dict) -> None:
    """An endpoint with a query, fragment or user name may carry a key, and the app refuses it:
    stop before anything is written. The error names the setting, never its value."""
    for key in ("llm_primary_api_base", "llm_fallback_api_base"):
        value = mapped["settings"][key]
        if not value:
            continue
        try:
            parts = urlsplit(str(value).strip())
            hidden = parts.query or parts.fragment or parts.username is not None or parts.password is not None
        except ValueError:
            hidden = True
        if hidden:
            raise ConvertError(f"settings.{key} must be an address with no query, fragment or user name")


def convert(config, criteria=None, tex=None, seen=None, state=None) -> dict:
    return _convert(config, criteria, tex, seen, state)[0]


def _convert(config, criteria, tex, seen, state):
    """The import object, and the files it was read from."""
    config = Path(config).resolve()
    values, present, unknown = effective_settings(config)
    mapped = {section: {key: _plain(values[key]) for key in keys} for section, keys in MAPPED.items()}
    _check_endpoints(mapped)
    mapped_paths = {f"{section}.{key}" for section, keys in MAPPED.items() for key in keys}

    all_sources = select_sources(values["sources_enable"], values["sources_disable"], generic=True)
    sources = [name for name in all_sources if name not in UNSUPPORTED_SOURCES]
    unsupported = set(unknown)
    unsupported |= {path for path in present if path not in mapped_paths and path not in CONSUMED}
    unsupported |= {f"source.{name}" for name in all_sources if name in UNSUPPORTED_SOURCES}

    criteria_path = criteria or _beside(config, values["criteria_file"])
    tex_path = tex or _beside(config, values["base_tex_file"])
    criteria_text = _read_text(criteria_path, bool(criteria))
    tex_text = _read_text(tex_path, True)
    history = None
    if seen:
        entries = _read_json(seen, "seen jobs")
        if not isinstance(entries, list):
            raise ConvertError(f"{seen} is not a JSON list")
        history = {"format": "jobhunter-history", "version": 1, "seen": convert_history(entries)}
    jobs = convert_jobs(_read_json(state, "job state")) if state else []

    inputs = [path for path in (config, criteria_path, tex_path, seen, state) if path is not None]
    return {
        "format": FORMAT,
        "version": VERSION,
        "name": config.stem,
        "values": mapped,
        "sources": sources,
        "criteria": criteria_text,
        "tex": tex_text,
        "history": history,
        "jobs": jobs,
        "unsupported": sorted(unsupported),
    }, inputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="the engine's settings TOML")
    parser.add_argument("--criteria", type=Path, help="criteria text (default: the TOML's criteria_file)")
    parser.add_argument("--tex", type=Path, help="base CV .tex (default: the TOML's base_tex_file)")
    parser.add_argument("--seen", type=Path, help="the engine's seen_jobs.json")
    parser.add_argument("--state", type=Path, help="the TUI's job_state.json")
    parser.add_argument("--out", type=Path, required=True, help="the import file to write")
    args = parser.parse_args(argv)
    try:
        result, inputs = _convert(args.config, args.criteria, args.tex, args.seen, args.state)
        out = args.out.resolve()
        if any(Path(path).resolve() == out for path in inputs):
            raise ConvertError(f"--out {args.out} is one of the input files")
    except ConvertError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    history = len(result["history"]["seen"]) if result["history"] else 0
    print(f"{len(result['sources'])} sources, {history} history keys, {len(result['jobs'])} jobs, "
          f"{len(result['unsupported'])} unsupported settings written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
