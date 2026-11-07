"""Commands beside the areas: import-bundle, legacy-check and fetch-postings.

All three run the engine's own code on files given as arguments; none has a
default path, so no private file is read unless it is named on the command line.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from job_search.config import PipelineConfig, load_criteria, require_search_configured
from job_search.evaluation_config import evaluation_configuration_revision
from job_search.identity import job_identity_keys
from job_search.models import coerce_job
from job_search.pipeline.stages import clean_job_description
from job_search.sources import ALL_SOURCES
from job_search.sources.fetch import fetch_jobs_with_health, select_sources
from job_search.sources.health import format_source_health
from job_search.state.seen_jobs import (
    SeenSet,
    criteria_fingerprint,
    evaluation_signature,
    lifecycle_record,
    should_reevaluate,
)

from . import canonical_json

_REPO_ROOT = Path(__file__).resolve().parents[2]

# run_daily's literal prompt revision (pipeline/run.py), not cfg.prompt_revision.
_DAILY_PROMPT_REVISION = "jev-1.13-approved-v1"


def engine_commit() -> str:
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()

    dirty = git("status", "--porcelain", "--untracked-files=no")
    return git("rev-parse", "HEAD") + ("-dirty" if dirty else "")


def load_config(toml_path) -> PipelineConfig:
    """The engine's config for one TOML, with no environment overrides."""
    environ = {"JOB_SEARCH_SETTINGS_FILE": str(Path(toml_path).resolve())}
    with mock.patch.dict(os.environ, environ, clear=True):
        return PipelineConfig.from_env()


def legacy_criteria_version(criteria_md: str, search, candidate, policy) -> str:
    """The criteria version run_daily stores into and compares against seen_jobs.json."""
    prompt_revision = _DAILY_PROMPT_REVISION + "\n" + evaluation_configuration_revision(
        search, candidate, policy
    )
    return criteria_fingerprint(criteria_md, prompt_revision)


def legacy_answer(seen, posting, version: str) -> dict:
    """One posting's legacy answer, decided as run_daily decides it."""
    job = coerce_job(posting)
    keys = job_identity_keys(job)
    signature = evaluation_signature(clean_job_description(job.description), version)
    if seen.isdisjoint(keys):
        answer = "new"
    elif should_reevaluate(seen, job, signature):
        answer = "reopened"
    else:
        answer = "suppressed"
    return {
        "url": job.url,
        "answer": answer,
        "signature": signature,
        "matched": signature in lifecycle_record(seen, **job).signatures,
    }


def legacy_answers(seen_entries, postings, version: str) -> list:
    seen = SeenSet(seen_entries)
    return [legacy_answer(seen, posting, version) for posting in postings]


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(obj), encoding="utf-8")


def import_bundle(toml, criteria, seen, job_state, out: Path) -> int:
    cfg = load_config(toml)
    criteria_md = load_criteria(criteria)
    seen_jobs = _read_json(seen)
    if not isinstance(seen_jobs, list):
        raise SystemExit(f"{seen}: seen_jobs must be a JSON array")
    state = _read_json(job_state)
    if not isinstance(state, dict):
        raise SystemExit(f"{job_state}: job_state must be a JSON object")
    bundle = {
        "seen_jobs": seen_jobs,
        "toml": Path(toml).read_text(encoding="utf-8"),
        "criteria_md": criteria_md,
        "job_state": state,
        "legacy_criteria_version": legacy_criteria_version(
            criteria_md, cfg.search, cfg.candidate, cfg.policy
        ),
        "engine_commit": engine_commit(),
    }
    _write_json(out / "bundle.json", bundle)
    print(f"wrote {out / 'bundle.json'}")
    return 0


def legacy_check(bundle_dir: Path, postings_path, out: Path) -> int:
    bundle = _read_json(bundle_dir / "bundle.json")
    postings = _read_json(postings_path)
    if not isinstance(postings, list):
        raise SystemExit(f"{postings_path}: postings must be a JSON array")
    answers = legacy_answers(bundle["seen_jobs"], postings, bundle["legacy_criteria_version"])
    _write_json(out, answers)
    print(f"wrote {len(answers)} answers to {out}")
    return 0


def fetch_postings(toml, sources, out: Path) -> int:
    cfg = load_config(toml)
    require_search_configured(cfg)
    if sources is None:
        names = select_sources(cfg.sources_enable, cfg.sources_disable, generic=True)
    else:
        names = [name.strip() for name in sources.split(",") if name.strip()]
        unknown = [name for name in names if name not in ALL_SOURCES]
        if unknown:
            raise SystemExit(f"unknown sources: {', '.join(unknown)}")
        if not names:
            raise SystemExit("--sources names no source")
    # As _fetch_for_pipeline: no relocation_regions, so the daily default applies.
    report = fetch_jobs_with_health(
        source_names=names,
        search=cfg.search,
        candidate=cfg.candidate,
        budget_seconds=cfg.scrape_budget_seconds,
        # Decision 10: honour the TOML's relocation_regions, as the Swift port does (the daily
        # path passes None, which falls back to the default eu/ca/us).
        relocation_regions=cfg.search.relocation_regions,
    )
    summary = format_source_health(report)
    if summary:
        print(summary, file=sys.stderr)
    if not report.has_usable_source:
        print("No usable job source completed; nothing written.", file=sys.stderr)
        return 1
    postings = [job.to_dict() for job in report.jobs]
    _write_json(out, postings)
    print(f"wrote {len(postings)} postings to {out}")
    return 0
