"""Source dispatch + result rendering: fetch_jobs, run_scraper, display helpers."""
import json
import os
import queue
import sys
import threading
import time

from ..config import MAX_WORKERS, SCRAPE_BUDGET_SECONDS
from ..filters import run_pipeline
from ..filters.rules import DEFAULT_RELOCATION_REGIONS
from ..models import REGION_LABELS, REGION_MAP, Region, job_to_dict
from ..text import unescape2
from . import ALL_SOURCES, SOURCE_DESCRIPTIONS
from .health import FetchReport, SourceHealth, SourceStatus, format_source_health


def parse_sources(value):
    if not value or str(value).strip().lower() == "all":
        return None
    requested = [part.strip().lower() for part in str(value).split(",") if part.strip()]
    valid = []
    invalid = []
    for name in requested:
        if name in ALL_SOURCES:
            valid.append(name)
        else:
            invalid.append(name)
    if invalid:
        print("Ignoring unknown sources: {}".format(", ".join(invalid)), file=sys.stderr)
    return valid


def default_source_names():
    """Names of the default-on sources, in registry order.

    A source is default-on unless it registered with ``default_enabled=False``.
    Uses ``getattr(..., True)`` so bare fake classes (as tests monkeypatch onto
    ALL_SOURCES) are treated as on."""
    return [name for name, cls in ALL_SOURCES.items() if getattr(cls, "default_enabled", True)]


def select_sources(enable=(), disable=()):
    """Resolve which sources to run from enable/disable name lists.

    A source runs when it is explicitly enabled, or when it is default-on and
    not explicitly disabled — so ``enable`` wins over ``disable`` for a name in
    both. Registry order is preserved. Unknown names in either list are warned
    about on stderr and ignored (parse_sources style). Empty lists reproduce
    default_source_names(), so the no-env path is unchanged."""
    enable = tuple(enable)
    disable = tuple(disable)
    known = set(ALL_SOURCES)
    unknown = []
    for name in enable + disable:
        if name not in known and name not in unknown:
            unknown.append(name)
    if unknown:
        print("Ignoring unknown sources: {}".format(", ".join(unknown)), file=sys.stderr)
    enabled_set = set(enable)
    disabled_set = set(disable)
    selected = []
    for name, cls in ALL_SOURCES.items():
        if name in enabled_set or (getattr(cls, "default_enabled", True) and name not in disabled_set):
            selected.append(name)
    return selected


def parse_regions(value):
    regions = set()
    for raw in str(value or "").split(","):
        key = raw.strip().lower()
        if not key:
            continue
        region = REGION_MAP.get(key)
        if region:
            regions.add(region)
        else:
            print("Ignoring unknown region: {}".format(raw), file=sys.stderr)
    return regions or set(DEFAULT_RELOCATION_REGIONS)


def source_names_for_display(source_names):
    if not source_names:
        return "all"
    return ", ".join(source_names)


def regions_for_display(regions):
    reverse = {value: key for key, value in REGION_MAP.items()}
    ordered = [Region.EU, Region.CA, Region.AU, Region.US]
    return ",".join(reverse[region] for region in ordered if region in regions)


def _fetch_source_with_diagnostics(source_name, source_cls, verbose):
    source = source_cls()
    if verbose:
        print("[{}] fetching...".format(source_name), flush=True)
    try:
        jobs = source.fetch(verbose=verbose)
        if verbose:
            print("[{}] done — {} job(s)".format(source_name, len(jobs)), flush=True)
        return source_name, jobs, None, source
    except Exception as exc:
        return source_name, [], exc, source


def fetch_source(source_name, source_cls, verbose):
    """Compatibility helper preserving the historical three-item result."""
    name, jobs, error, _source = _fetch_source_with_diagnostics(source_name, source_cls, verbose)
    return name, jobs, error


def _budget_seconds_from_env(default):
    """Per-run override via SCRAPE_BUDGET_SECONDS; falls back to `default` when
    unset, non-numeric, or non-positive (a malformed value must never disable
    the guard)."""
    raw = os.environ.get("SCRAPE_BUDGET_SECONDS")
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _source_health(name, jobs, error, source):
    if error is not None:
        return SourceHealth(name, SourceStatus.FAILED, failure_detail=" ".join(str(error).split())[:240])
    if source._skip_detail:
        return SourceHealth(name, SourceStatus.SKIPPED, failure_detail=source._skip_detail)
    attempts = source._attempts
    failures = source._failures
    if source._timeout_detail:
        successes = attempts - len(failures)
        status = SourceStatus.PARTIAL if successes else SourceStatus.TIMED_OUT
        return SourceHealth(
            name, status, len(jobs), attempts, source._timeout_detail,
        )
    if attempts == 0:
        return SourceHealth(name, SourceStatus.SUCCESS, len(jobs), 0)
    successes = attempts - len(failures)
    status = SourceStatus.PARTIAL if failures and successes else (
        SourceStatus.FAILED if failures else SourceStatus.SUCCESS
    )
    return SourceHealth(name, status, len(jobs), attempts, failures[-1] if failures else "")


def fetch_jobs_with_health(source_names=None, relocation_regions=None, max_age=30, verbose=False, budget_seconds=None):
    """Fetch and filter jobs, returning the list of Job objects.

    Sources run concurrently under a wall-clock budget: any source still running
    when the budget elapses is abandoned (left on a daemon thread the interpreter
    reaps at exit) and the run proceeds with whatever the others returned. This
    stops one throttled source — historically LinkedIn via jobspy — from holding
    the whole pipeline hostage until the CI job timeout cancels it.
    """
    if budget_seconds is None:
        budget_seconds = _budget_seconds_from_env(SCRAPE_BUDGET_SECONDS)
    selected_names = source_names or default_source_names()
    selected = [(name, ALL_SOURCES[name]) for name in selected_names if name in ALL_SOURCES]

    if not selected:
        return FetchReport((), ())

    if verbose:
        print("Scraping {} sources: {}...".format(len(selected), ", ".join(name for name, _ in selected)), flush=True)

    # One daemon thread per source, concurrency capped at MAX_WORKERS via a
    # semaphore. Daemon threads are essential: a slow source we abandon must not
    # block interpreter exit (a plain ThreadPoolExecutor joins its non-daemon
    # workers at exit, which would re-introduce the very hang we're preventing).
    results = queue.Queue()
    limiter = threading.Semaphore(min(MAX_WORKERS, len(selected)))

    def worker(name, source_cls):
        with limiter:
            results.put(_fetch_source_with_diagnostics(name, source_cls, verbose))

    for name, source_cls in selected:
        threading.Thread(
            target=worker, args=(name, source_cls),
            name="fetch-{}".format(name), daemon=True,
        ).start()

    raw_jobs = []
    completed = set()
    outcomes_by_name = {}
    deadline = time.monotonic() + budget_seconds
    while len(completed) < len(selected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            source_name, jobs, error, source = results.get(timeout=remaining)
        except queue.Empty:
            break
        completed.add(source_name)
        outcomes_by_name[source_name] = _source_health(source_name, jobs, error, source)
        if error is not None:
            if verbose:
                print("[{}] Failed: {}".format(source_name, error), file=sys.stderr)
            continue
        raw_jobs.extend(jobs)

    abandoned = [name for name, _ in selected if name not in completed]
    for name in abandoned:
        outcomes_by_name[name] = SourceHealth(
            name, SourceStatus.TIMED_OUT,
            failure_detail="scrape budget of {:g}s exceeded".format(budget_seconds),
        )
    if abandoned:
        # Always surface (not just under verbose): a truncated fetch is
        # operationally significant, and naming the culprit aids diagnosis.
        print("[fetch] Scrape budget of {:g}s exceeded; abandoned slow source(s): {}. "
              "Proceeding with {}/{} source(s).".format(
                  budget_seconds, ", ".join(abandoned), len(completed), len(selected)),
              file=sys.stderr, flush=True)

    if verbose:
        print("Raw jobs collected: {}".format(len(raw_jobs)), flush=True)

    filtered = run_pipeline(
        raw_jobs,
        max_age_days=max_age,
        relocation_regions=relocation_regions or DEFAULT_RELOCATION_REGIONS,
    )

    if verbose:
        print("After filtering: {}".format(len(filtered)), flush=True)

    outcomes = tuple(outcomes_by_name[name] for name, _ in selected)
    return FetchReport(tuple(filtered), outcomes)


def fetch_jobs(source_names=None, relocation_regions=None, max_age=30, verbose=False, budget_seconds=None):
    """Compatibility wrapper returning only the filtered job list."""
    return list(fetch_jobs_with_health(
        source_names=source_names,
        relocation_regions=relocation_regions,
        max_age=max_age,
        verbose=verbose,
        budget_seconds=budget_seconds,
    ).jobs)


def run_scraper(source_names=None, relocation_regions=None, max_age=30, as_json=False, verbose=False):
    selected_names = source_names or default_source_names()
    selected = [(name, ALL_SOURCES[name]) for name in selected_names if name in ALL_SOURCES]

    if not selected:
        print("No valid sources selected.", file=sys.stderr)
        return 2

    if not as_json and not verbose:
        print("Scraping {} sources: {}...".format(len(selected), ", ".join(name for name, _ in selected)))

    report = fetch_jobs_with_health(
        source_names=source_names,
        relocation_regions=relocation_regions,
        max_age=max_age,
        verbose=verbose,
    )

    print(format_source_health(report), file=sys.stderr)
    if not report.has_usable_source:
        print("No usable job source completed; aborting.", file=sys.stderr)
        return 1

    render_jobs(report.jobs, as_json=as_json)
    return 0


def render_jobs(jobs, as_json=False):
    if as_json:
        print(json.dumps([job_to_dict(job) for job in jobs], indent=2))
        return

    if not jobs:
        print("No matching jobs found.")
        return

    grouped = {}
    for job in jobs:
        grouped.setdefault(job.region, []).append(job)

    print("")
    print("Found {} matching jobs".format(len(jobs)))
    print("")

    for region in [Region.EU, Region.CA, Region.AU, Region.US, Region.UNKNOWN]:
        region_jobs = grouped.get(region)
        if not region_jobs:
            continue

        label = REGION_LABELS[region]
        print("=== {} ({} jobs) ===".format(label, len(region_jobs)))
        print("")

        for index, job in enumerate(region_jobs):
            title = unescape2(job.title)
            company = unescape2(job.company) or "Unknown company"
            date_str = job.date_posted.isoformat() if job.date_posted else "n/a"
            remote = " (remote)" if job.is_remote else ""
            skills = ", ".join(job.matched_skills[:4])

            print("  {}{}".format(title, remote))
            print("    {} | {} | {} | {}".format(company, job.location or "Unknown location", job.source, date_str))
            if skills:
                print("    skills: {}".format(skills))
            if job.url:
                print("    {}".format(job.url))
            if index < len(region_jobs) - 1:
                print("")
        print("")


def print_sources():
    print("Available sources:")
    for name in ALL_SOURCES:
        marker = "" if getattr(ALL_SOURCES[name], "default_enabled", True) else "  (default: off)"
        print("  {:18} {}{}".format(name, SOURCE_DESCRIPTIONS.get(name, ""), marker))
