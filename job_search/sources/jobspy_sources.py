"""JobSpy-backed sources (jobspy, linkedin-global, linkedin-israel).

The `jobspy` package is imported lazily inside fetch(), so the module imports
cleanly (and the registry builds) even when python-jobspy is not installed.
"""
import sys

from ..dates import parse_iso_date
from ..models import Job
from .base import BaseSource, register


def _cell(row, key):
    """Read a value from a jobspy DataFrame row, treating None/NaN as empty string."""
    value = row.get(key)
    if value is None or value != value:  # NaN != NaN
        return ""
    return str(value).strip()


def _row_is_remote(row):
    """Read is_remote from a jobspy DataFrame row, treating None/NaN as False."""
    value = row.get("is_remote")
    if value is None or value != value:  # NaN != NaN
        return False
    return bool(value)


SEARCH_QUERIES = [
    "iOS developer relocation",
    "macOS engineer relocation",
    "SwiftUI iOS engineer visa sponsorship",
    "Apple platform engineer relocation",
    "Objective-C iOS developer visa sponsorship",
]

JOBSPY_SITES = ["indeed", "google"]
COUNTRY_SEARCHES = [
    {"country_indeed": "Germany"},
    {"country_indeed": "Netherlands"},
    {"country_indeed": "Portugal"},
    {"country_indeed": "Spain"},
    {"country_indeed": "United Kingdom"},
    {"country_indeed": "Canada"},
    {"country_indeed": "United States"},
    {"country_indeed": "Ireland"},
    {"country_indeed": "Switzerland"},
    {"country_indeed": "France"},
    {"country_indeed": "Italy"},
    {"country_indeed": "Poland"},
]


@register(
    "Optional JobSpy search. Skips automatically unless python-jobspy is installed.",
    optional_dependency="python-jobspy",
)
class JobSpySource(BaseSource):
    name = "jobspy"

    def fetch(self, verbose=False):
        try:
            from jobspy import scrape_jobs
        except ImportError:
            if verbose:
                print("[jobspy] Skipped: optional package 'python-jobspy' is not installed")
            return []

        all_jobs = []
        for query in SEARCH_QUERIES:
            for country in COUNTRY_SEARCHES:
                try:
                    df = scrape_jobs(
                        site_name=JOBSPY_SITES,
                        search_term=query,
                        results_wanted=15,
                        hours_old=720,
                        **country
                    )
                except Exception as exc:
                    if verbose:
                        print("[jobspy] Search error for {!r} {}: {}".format(query, country, exc))
                    continue

                for _, row in df.iterrows():
                    posted = None
                    row_date = row.get("date_posted", None)
                    if row_date is not None:
                        try:
                            posted = row_date.date() if hasattr(row_date, "date") else parse_iso_date(row_date)
                        except Exception:
                            posted = parse_iso_date(str(row_date)[:10])
                    all_jobs.append(
                        Job(
                            title=str(row.get("title", "")),
                            company=str(row.get("company", "")),
                            location=str(row.get("location", "")),
                            url=str(row.get("job_url", "")),
                            source=self.name,
                            date_posted=posted,
                            description=str(row.get("description", "")),
                            is_remote=bool(row.get("is_remote", False)),
                        )
                    )

        if verbose:
            print("[jobspy] Fetched {} raw jobs".format(len(all_jobs)))
        return all_jobs


LINKEDIN_ISRAEL_QUERIES = [
    "iOS",
    "macOS",
]

LINKEDIN_GLOBAL_QUERIES = [
    "iOS",
    "macOS",
]

# (location, results_wanted) — EU is region-wide (all member states in one
# search), so it gets a deeper pull than a single country like Canada.
# Israel is intentionally excluded here: it's handled by LinkedInIsraelSource.
LINKEDIN_GLOBAL_LOCATIONS = [
    ("European Union", 75),
    ("Canada", 25),
]


@register(
    "LinkedIn iOS/Swift jobs globally (Remote + key relocation countries) via JobSpy. "
    "Skips unless python-jobspy is installed.",
    optional_dependency="python-jobspy",
)
class LinkedInGlobalSource(BaseSource):
    name = "linkedin-global"

    def fetch(self, verbose=False):
        try:
            from jobspy import scrape_jobs
        except ImportError:
            if verbose:
                print("[linkedin-global] Skipped: python-jobspy not installed")
            return []

        all_jobs = []
        total = len(LINKEDIN_GLOBAL_QUERIES) * len(LINKEDIN_GLOBAL_LOCATIONS)
        step = 0
        failures = 0
        for query in LINKEDIN_GLOBAL_QUERIES:
            for location, results_wanted in LINKEDIN_GLOBAL_LOCATIONS:
                step += 1
                if verbose:
                    print("[linkedin-global] ({}/{}) {!r} in {}...".format(step, total, query, location), flush=True)
                try:
                    df = scrape_jobs(
                        site_name=["linkedin"],
                        search_term=query,
                        location=location,
                        results_wanted=results_wanted,
                        hours_old=48,
                        linkedin_fetch_description=True,
                    )
                except Exception as exc:
                    failures += 1
                    print("[linkedin-global] Error for {!r} {!r}: {}".format(query, location, exc), file=sys.stderr)
                    continue

                for _, row in df.iterrows():
                    posted = None
                    row_date = row.get("date_posted", None)
                    if row_date is not None:
                        try:
                            posted = row_date.date() if hasattr(row_date, "date") else parse_iso_date(row_date)
                        except Exception:
                            posted = parse_iso_date(str(row_date)[:10])
                    all_jobs.append(
                        Job(
                            title=_cell(row, "title"),
                            company=_cell(row, "company"),
                            location=_cell(row, "location"),
                            url=_cell(row, "job_url"),
                            source=self.name,
                            date_posted=posted,
                            description=_cell(row, "description"),
                            is_remote=_row_is_remote(row),
                        )
                    )

        if failures:
            print("[linkedin-global] Warning: {}/{} query/location combos failed".format(failures, total), file=sys.stderr)
        if verbose:
            print("[linkedin-global] Fetched {} raw jobs".format(len(all_jobs)))
        return all_jobs


@register(
    "LinkedIn iOS/Swift jobs in Israel via JobSpy. Skips unless python-jobspy is installed.",
    optional_dependency="python-jobspy",
)
class LinkedInIsraelSource(BaseSource):
    name = "linkedin-israel"

    def fetch(self, verbose=False):
        try:
            from jobspy import scrape_jobs
        except ImportError:
            if verbose:
                print("[linkedin-israel] Skipped: python-jobspy not installed")
            return []

        all_jobs = []
        total = len(LINKEDIN_ISRAEL_QUERIES)
        failures = 0
        for step, query in enumerate(LINKEDIN_ISRAEL_QUERIES, 1):
            if verbose:
                print("[linkedin-israel] ({}/{}) {!r} in Israel...".format(step, total, query), flush=True)
            try:
                df = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=query,
                    location="Israel",
                    results_wanted=25,
                    hours_old=48,
                    linkedin_fetch_description=True,
                )
            except Exception as exc:
                failures += 1
                print("[linkedin-israel] Error for {!r}: {}".format(query, exc), file=sys.stderr)
                continue

            for _, row in df.iterrows():
                posted = None
                row_date = row.get("date_posted", None)
                if row_date is not None:
                    try:
                        posted = row_date.date() if hasattr(row_date, "date") else parse_iso_date(row_date)
                    except Exception:
                        posted = parse_iso_date(str(row_date)[:10])
                all_jobs.append(
                    Job(
                        title=_cell(row, "title"),
                        company=_cell(row, "company"),
                        location=_cell(row, "location"),
                        url=_cell(row, "job_url"),
                        source=self.name,
                        date_posted=posted,
                        description=_cell(row, "description"),
                        is_remote=_row_is_remote(row),
                    )
                )

        if failures:
            print("[linkedin-israel] Warning: {}/{} queries failed".format(failures, total), file=sys.stderr)
        if verbose:
            print("[linkedin-israel] Fetched {} raw jobs".format(len(all_jobs)))
        return all_jobs
