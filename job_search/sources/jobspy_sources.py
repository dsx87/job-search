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


def _configured_values(source, names, fallback):
    """Read a non-empty tuple from the generic source search configuration."""
    search = getattr(source, "search", None)
    if search is None:
        return tuple(fallback)
    for name in names:
        values = tuple(
            str(value).strip() for value in getattr(search, name, ()) if str(value).strip()
        )
        if values:
            return values
    return ()


def _configured_result_limit(source, fallback):
    search = getattr(source, "search", None)
    value = getattr(search, "results_per_query", fallback) if search is not None else fallback
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _generic_jobspy_requests(location):
    """Build valid provider calls for a city, country, or regional label."""
    country_names = {
        "AR": "Argentina", "AU": "Australia", "AT": "Austria", "BE": "Belgium",
        "BR": "Brazil", "CA": "Canada", "CL": "Chile", "CN": "China",
        "CO": "Colombia", "CR": "Costa Rica", "CZ": "Czech Republic", "DK": "Denmark",
        "EC": "Ecuador", "EG": "Egypt", "FI": "Finland", "FR": "France",
        "DE": "Germany", "GR": "Greece", "HK": "Hong Kong", "HU": "Hungary",
        "IN": "India", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel",
        "IT": "Italy", "JP": "Japan", "KW": "Kuwait", "LU": "Luxembourg",
        "MY": "Malaysia", "MX": "Mexico", "MA": "Morocco", "NL": "Netherlands",
        "NZ": "New Zealand", "NG": "Nigeria", "NO": "Norway", "OM": "Oman",
        "PK": "Pakistan", "PA": "Panama", "PE": "Peru", "PH": "Philippines",
        "PL": "Poland", "PT": "Portugal", "QA": "Qatar", "RO": "Romania",
        "SA": "Saudi Arabia", "SG": "Singapore", "ZA": "South Africa", "KR": "South Korea",
        "ES": "Spain", "SE": "Sweden", "CH": "Switzerland", "TW": "Taiwan",
        "TH": "Thailand", "TR": "Turkey", "UA": "Ukraine", "AE": "United Arab Emirates",
        "GB": "UK", "US": "USA", "UY": "Uruguay", "VE": "Venezuela", "VN": "Vietnam",
    }
    try:
        from ..location.countries import advertised_locations
    except ImportError:
        advertised = ()
    else:
        advertised = advertised_locations(location)
    country = next(
        (country_names.get(str(getattr(target, "country", "")).upper(), "") for target in advertised
         if country_names.get(str(getattr(target, "country", "")).upper(), "")),
        "",
    )
    if country:
        return ({"site_name": list(JOBSPY_SITES), "location": location, "country_indeed": country},)
    # JobSpy documents ``location`` as the globally supported search input;
    # Indeed needs a country enum, so retain a Google-only regional/city query.
    return ({"site_name": ["google"], "location": location},)


# Two broad queries replace the previous five near-duplicates (all "iOS/Apple +
# relocation/visa"). Indeed/Google full-text search already matches Swift/SwiftUI/
# Objective-C/macOS within these, so the extra permutations mostly re-fetched the
# same postings — cutting per-run JobSpy calls from 5×12×2 to 2×12×2 (audit
# Finding 12).
SEARCH_QUERIES = [
    "iOS developer relocation visa sponsorship",
    "macOS Swift engineer relocation",
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
        if getattr(self, "search", None) is not None and (
            not _configured_values(self, ("search_terms",), ())
            or not _configured_values(self, ("query_locations",), ())
        ):
            self._skip("configured search requires search_terms and query_locations")
            return []
        try:
            from jobspy import scrape_jobs
        except ImportError:
            self._skip("optional package 'python-jobspy' is not installed")
            if verbose:
                print("[jobspy] Skipped: optional package 'python-jobspy' is not installed")
            return []

        all_jobs = []
        queries = _configured_values(self, ("search_terms",), SEARCH_QUERIES)
        configured_locations = _configured_values(self, ("query_locations",), ())
        countries = (
            tuple(request for location in configured_locations for request in _generic_jobspy_requests(location))
            if getattr(self, "search", None) is not None
            else tuple(COUNTRY_SEARCHES)
        )
        results_wanted = _configured_result_limit(self, 15)
        for query in queries:
            for country in countries:
                try:
                    df = scrape_jobs(
                        search_term=query,
                        results_wanted=results_wanted,
                        hours_old=720,
                        **country
                    )
                    self._attempt_succeeded()
                except Exception as exc:
                    self._attempt_failed(exc)
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

# Lookback window for the LinkedIn searches. 48 h matched the daily cadence but
# left no slack: any outage longer than two days is a permanent gap, because a
# posting older than the window is never offered again. A week costs nothing —
# already-seen postings are dropped by the dedup state before evaluation, and
# filters.filter_by_age still enforces the real freshness rule.
LINKEDIN_HOURS_OLD = 168


@register(
    "LinkedIn jobs using configured search terms and locations via JobSpy. "
    "Skips unless python-jobspy is installed.",
    optional_dependency="python-jobspy",
)
class LinkedInGlobalSource(BaseSource):
    name = "linkedin-global"

    def fetch(self, verbose=False):
        if getattr(self, "search", None) is not None and (
            not _configured_values(self, ("search_terms",), ())
            or not _configured_values(self, ("query_locations",), ())
        ):
            self._skip("configured search requires search_terms and query_locations")
            return []
        try:
            from jobspy import scrape_jobs
        except ImportError:
            self._skip("optional package 'python-jobspy' is not installed")
            if verbose:
                print("[linkedin-global] Skipped: python-jobspy not installed")
            return []

        all_jobs = []
        queries = _configured_values(self, ("search_terms",), LINKEDIN_GLOBAL_QUERIES)
        configured_locations = _configured_values(self, ("query_locations",), ())
        locations = (
            tuple((location, _configured_result_limit(self, 25)) for location in configured_locations)
            if getattr(self, "search", None) is not None
            else tuple(LINKEDIN_GLOBAL_LOCATIONS)
        )
        total = len(queries) * len(locations)
        step = 0
        failures = 0
        for query in queries:
            for location, results_wanted in locations:
                step += 1
                if verbose:
                    print("[linkedin-global] ({}/{}) {!r} in {}...".format(step, total, query, location), flush=True)
                try:
                    df = scrape_jobs(
                        site_name=["linkedin"],
                        search_term=query,
                        location=location,
                        results_wanted=results_wanted,
                        hours_old=LINKEDIN_HOURS_OLD,
                        linkedin_fetch_description=True,
                    )
                    self._attempt_succeeded()
                except Exception as exc:
                    self._attempt_failed(exc)
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
    "Optional LinkedIn Israel adapter; uses configured queries when selected. Requires python-jobspy.",
    optional_dependency="python-jobspy",
)
class LinkedInIsraelSource(BaseSource):
    name = "linkedin-israel"
    generic_default_enabled = False

    def fetch(self, verbose=False):
        if getattr(self, "search", None) is not None and (
            not _configured_values(self, ("search_terms",), ())
            or not _configured_values(self, ("query_locations",), ())
        ):
            self._skip("configured search requires search_terms and query_locations")
            return []
        try:
            from jobspy import scrape_jobs
        except ImportError:
            self._skip("optional package 'python-jobspy' is not installed")
            if verbose:
                print("[linkedin-israel] Skipped: python-jobspy not installed")
            return []

        all_jobs = []
        queries = _configured_values(self, ("search_terms",), LINKEDIN_ISRAEL_QUERIES)
        locations = _configured_values(self, ("query_locations",), ("Israel",))
        total = len(queries) * len(locations)
        failures = 0
        step = 0
        for query in queries:
            for location in locations:
                step += 1
                if verbose:
                    print("[linkedin-israel] ({}/{}) {!r} in {}...".format(step, total, query, location), flush=True)
                try:
                    df = scrape_jobs(
                        site_name=["linkedin"],
                        search_term=query,
                        location=location,
                        results_wanted=_configured_result_limit(self, 25),
                        hours_old=LINKEDIN_HOURS_OLD,
                        linkedin_fetch_description=True,
                    )
                    self._attempt_succeeded()
                except Exception as exc:
                    self._attempt_failed(exc)
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
