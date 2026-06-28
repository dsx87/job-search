"""Source dispatch + result rendering: fetch_jobs, run_scraper, display helpers."""
import concurrent.futures
import json
import sys

from ..config import MAX_WORKERS
from ..filters import run_pipeline
from ..filters.rules import DEFAULT_RELOCATION_REGIONS
from ..models import REGION_LABELS, REGION_MAP, Region, job_to_dict
from ..text import unescape2
from . import ALL_SOURCES, SOURCE_DESCRIPTIONS


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


def fetch_source(source_name, source_cls, verbose):
    source = source_cls()
    if verbose:
        print("[{}] fetching...".format(source_name), flush=True)
    try:
        jobs = source.fetch(verbose=verbose)
        if verbose:
            print("[{}] done — {} job(s)".format(source_name, len(jobs)), flush=True)
        return source_name, jobs, None
    except Exception as exc:
        return source_name, [], exc


def fetch_jobs(source_names=None, relocation_regions=None, max_age=30, verbose=False):
    """Fetch and filter jobs, returning the list of Job objects."""
    selected_names = source_names or list(ALL_SOURCES.keys())
    selected = [(name, ALL_SOURCES[name]) for name in selected_names if name in ALL_SOURCES]

    if not selected:
        return []

    if verbose:
        print("Scraping {} sources: {}...".format(len(selected), ", ".join(name for name, _ in selected)), flush=True)

    raw_jobs = []
    max_workers = min(MAX_WORKERS, len(selected))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(fetch_source, name, source_cls, verbose)
            for name, source_cls in selected
        ]
        for future in concurrent.futures.as_completed(futures):
            source_name, jobs, error = future.result()
            if error is not None:
                if verbose:
                    print("[{}] Failed: {}".format(source_name, error), file=sys.stderr)
                continue
            raw_jobs.extend(jobs)

    if verbose:
        print("Raw jobs collected: {}".format(len(raw_jobs)), flush=True)

    filtered = run_pipeline(
        raw_jobs,
        max_age_days=max_age,
        relocation_regions=relocation_regions or DEFAULT_RELOCATION_REGIONS,
    )

    if verbose:
        print("After filtering: {}".format(len(filtered)), flush=True)

    return filtered


def run_scraper(source_names=None, relocation_regions=None, max_age=30, as_json=False, verbose=False):
    selected_names = source_names or list(ALL_SOURCES.keys())
    selected = [(name, ALL_SOURCES[name]) for name in selected_names if name in ALL_SOURCES]

    if not selected:
        print("No valid sources selected.", file=sys.stderr)
        return 2

    if not as_json and not verbose:
        print("Scraping {} sources: {}...".format(len(selected), ", ".join(name for name, _ in selected)))

    jobs = fetch_jobs(
        source_names=source_names,
        relocation_regions=relocation_regions,
        max_age=max_age,
        verbose=verbose,
    )

    render_jobs(jobs, as_json=as_json)
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
        print("  {:18} {}".format(name, SOURCE_DESCRIPTIONS.get(name, "")))
