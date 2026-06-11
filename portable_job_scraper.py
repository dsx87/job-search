#!/usr/bin/env python3
"""
Portable iOS/macOS job scraper.

This is a single-file version of the scraper that uses only Python's standard
library by default. You can copy this file to another machine and run:

    python3 portable_job_scraper.py

It keeps the filtering behavior from the package version: find Apple-platform
developer roles, keep remote jobs globally, and keep relocation/visa jobs in
selected regions.
"""

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import enum
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


HTTP_TIMEOUT_SECONDS = 30
MAX_WORKERS = 8


class Region(enum.Enum):
    EU = "EU"
    CA = "CA"
    AU = "AU"
    US = "US"
    UNKNOWN = "UNKNOWN"


class Job(object):
    def __init__(
        self,
        title="",
        company="",
        location="",
        url="",
        source="",
        date_posted=None,
        description="",
        is_remote=False,
        region=Region.UNKNOWN,
        matched_skills=None,
    ):
        self.title = str(title or "").strip()
        self.company = str(company or "").strip()
        self.location = str(location or "").strip()
        self.url = str(url or "").strip()
        self.source = str(source or "").strip()
        self.date_posted = date_posted
        self.description = str(description or "")
        self.is_remote = bool(is_remote)
        self.region = region
        self.matched_skills = list(matched_skills or [])


SKILL_KEYWORDS = [
    "ios",
    "ipados",
    "macos",
    "iphone",
    "ipad",
    "swift",
    "objective-c",
    "objc",
    "swiftui",
    "uikit",
    "appkit",
    "xcode",
    "apple developer",
    "apple platform",
    "watchos",
    "tvos",
    "cocoa",
    "cocoa touch",
    "core data",
    "combine framework",
    "swift concurrency",
    "app store",
]

RELOCATION_KEYWORDS = [
    "relocation",
    "relocation support",
    "relocation package",
    "relocation assistance",
    "visa sponsorship",
    "visa support",
    "work permit",
    "immigration support",
    "relocate",
    "moving allowance",
    "sponsor visa",
    "sponsorship available",
    "work authorization support",
]

DEFAULT_RELOCATION_REGIONS = set([Region.EU, Region.CA, Region.US])
RELOCATION_GUARANTEED_SOURCES = set(["relocate.me"])

EU_COUNTRIES = set(
    [
        "germany",
        "netherlands",
        "spain",
        "portugal",
        "uk",
        "gb",
        "eng",
        "sct",
        "united kingdom",
        "england",
        "scotland",
        "wales",
        "northern ireland",
        "ireland",
        "sweden",
        "denmark",
        "finland",
        "austria",
        "switzerland",
        "france",
        "italy",
        "poland",
        "czech republic",
        "czechia",
        "belgium",
        "luxembourg",
        "norway",
        "estonia",
        "latvia",
        "lithuania",
        "romania",
        "bulgaria",
        "croatia",
        "hungary",
        "slovakia",
        "slovenia",
        "greece",
        "cyprus",
        "malta",
        "europe",
        "european union",
        "eu",
        "eea",
        "emea",
    ]
)

EU_CITIES = set(
    [
        "berlin",
        "munich",
        "hamburg",
        "frankfurt",
        "amsterdam",
        "rotterdam",
        "barcelona",
        "madrid",
        "lisbon",
        "porto",
        "london",
        "dublin",
        "stockholm",
        "copenhagen",
        "helsinki",
        "vienna",
        "zurich",
        "paris",
        "lyon",
        "milan",
        "rome",
        "warsaw",
        "prague",
        "brussels",
        "oslo",
        "tallinn",
        "riga",
        "vilnius",
        "bucharest",
        "budapest",
        "athens",
    ]
)

IL_LOCATIONS = set(
    [
        "israel",
        "tel aviv",
        "tel-aviv",
        "haifa",
        "jerusalem",
        "herzliya",
        "raanana",
        "ra'anana",
        "petah tikva",
        "petah-tikva",
        "beer sheva",
        "beer-sheva",
        "netanya",
        "rehovot",
        "rishon lezion",
        "rishon le-zion",
        "holon",
        "bnei brak",
        "modi'in",
        "modiin",
        "kfar saba",
        "rosh haayin",
        "yehud",
        "givat shmuel",
        "airport city",
        "lod",
        "ramla",
        "yokneam",
        "caesarea",
        "or yehuda",
        "kiryat gat",
        "ashkelon",
        "ashdod",
        "eilat",
    ]
)

CA_LOCATIONS = set(
    [
        "canada",
        "toronto",
        "vancouver",
        "montreal",
        "ottawa",
        "calgary",
        "edmonton",
        "winnipeg",
        "quebec",
        "british columbia",
        "ontario",
        "alberta",
    ]
)

AU_LOCATIONS = set(
    [
        "australia",
        "sydney",
        "melbourne",
        "brisbane",
        "perth",
        "adelaide",
        "canberra",
        "hobart",
        "darwin",
        "gold coast",
        "new south wales",
        "victoria",
        "queensland",
    ]
)

US_LOCATIONS = set(
    [
        "united states",
        "usa",
        "us",
        "u.s.",
        "u.s.a.",
        "new york",
        "san francisco",
        "los angeles",
        "seattle",
        "austin",
        "chicago",
        "boston",
        "denver",
        "miami",
        "atlanta",
        "dallas",
        "houston",
        "phoenix",
        "portland",
        "san diego",
        "san jose",
        "california",
        "texas",
        "washington",
        "massachusetts",
        "colorado",
        "florida",
        "georgia",
        "illinois",
        "pennsylvania",
        "virginia",
        "north carolina",
        "oregon",
        "arizona",
        "new jersey",
        "minnesota",
        "tennessee",
        "ohio",
        "michigan",
        "maryland",
        "connecticut",
        "utah",
    ]
)


REGION_MAP = {
    "eu": Region.EU,
    "ca": Region.CA,
    "au": Region.AU,
    "us": Region.US,
}

REGION_LABELS = {
    Region.EU: "Europe",
    Region.CA: "Canada",
    Region.AU: "Australia",
    Region.US: "United States",
    Region.UNKNOWN: "Other / Unknown",
}


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOUNDARY_KEYWORDS = set(
    [
        "ios",
        "ipados",
        "iphone",
        "ipad",
        "macos",
        "swift",
        "swiftui",
        "uikit",
        "appkit",
        "xcode",
        "watchos",
        "tvos",
        "cocoa",
        "objc",
    ]
)

_SKILL_PATTERNS = {}
for _kw in SKILL_KEYWORDS:
    if _kw in _BOUNDARY_KEYWORDS:
        _SKILL_PATTERNS[_kw] = re.compile(
            r"\b" + re.escape(_kw) + r"\b",
            re.IGNORECASE,
        )

_ENGINEERING_TITLE_RE = re.compile(
    r"\b(?:engineer|developer|architect|programmer|swe|software|application|apps?)\b",
    re.IGNORECASE,
)

_APPLE_ROLE_TITLE_RE = re.compile(
    r"\b(?:ios|ipados|macos|iphone|ipad|swiftui|uikit|appkit|objective-c|objc)\b",
    re.IGNORECASE,
)

_PLATFORM_KEYWORDS = set(
    [
        "ios",
        "ipados",
        "macos",
        "iphone",
        "ipad",
        "watchos",
        "tvos",
        "apple developer",
        "apple platform",
    ]
)

_APPLE_FRAMEWORK_KEYWORDS = set(
    [
        "swiftui",
        "uikit",
        "appkit",
        "xcode",
        "cocoa",
        "cocoa touch",
        "core data",
        "combine framework",
        "app store",
    ]
)

_LANGUAGE_KEYWORDS = set(["swift", "objective-c", "objc"])
_STRONG_TITLE_LANGUAGE_KEYWORDS = set(["objective-c", "objc"])

_NON_APPLE_STACK_KEYWORDS = set(
    [
        "android",
        "kotlin",
        "flutter",
        "react native",
        "xamarin",
        "ionic",
    ]
)

_REMOTE_KEYWORDS = set(
    [
        "remote",
        "remote-first",
        "work from home",
        "work-from-home",
        "distributed",
        "async",
        "anywhere",
        "home office",
    ]
)

_HYBRID_OR_ONSITE_KEYWORDS = set(
    [
        "hybrid",
        "on-site",
        "onsite",
        "in office",
        "in-office",
        "office-based",
    ]
)

_RELOCATION_BLOCKERS = set(
    [
        "cannot sponsor",
        "can't sponsor",
        "unable to sponsor",
        "do not sponsor",
        "does not sponsor",
        "no visa sponsorship",
        "no sponsorship",
        "sponsorship is not available",
        "visa sponsorship is not available",
        "without current or future sponsorship",
        "must be authorized to work",
    ]
)

_SHORT_LOCATION_TOKENS = set(["eu", "uk", "gb", "eng", "sct", "us", "u.s.", "u.s.a."])

_INDIA_LOCATION_RE = re.compile(
    r"(?<![a-z])(?:india|bengaluru|bangalore|mumbai|delhi|new delhi|hyderabad|"
    r"pune|chennai|kolkata|gurgaon|gurugram|noida)(?![a-z])",
    re.IGNORECASE,
)

_EXCLUDED_TITLE_RE = re.compile(
    r"\b(?:qa|quality assurance|test engineer|sdet|support engineer|"
    r"technical support|sales|account manager|recruiter|"
    r"account director|director|program manager|project manager|product manager|"
    r"engineering manager|quality automation|devops|site reliability|sre|"
    r"customer success|business development|writer|advocate|evangelist|"
    r"pest|technician|field service|mechanical designer|"
    r"data scientist|data science|data analyst|machine learning engineer|"
    r"ml engineer|ai engineer|ai/ml engineer)\b",
    re.IGNORECASE,
)


def collapse_ws(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_html(text):
    return html.unescape(_HTML_TAG_RE.sub(" ", str(text or "")))


def unescape2(text):
    result = html.unescape(str(text or ""))
    if "&" in result:
        result = html.unescape(result)
    return result


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


def match_keywords(text, keywords):
    matches = []
    for keyword in keywords:
        if keyword in _BOUNDARY_KEYWORDS:
            pattern = _SKILL_PATTERNS.get(keyword)
            if pattern and pattern.search(text):
                matches.append(keyword)
        elif keyword in text:
            matches.append(keyword)
    return matches


def job_text(job):
    return " ".join(
        part
        for part in (job.title, job.location, strip_html(job.description))
        if part
    ).lower()


def india_exclusion_filter(job):
    return not _INDIA_LOCATION_RE.search(job_text(job))


def role_filter(job):
    title = job.title.strip()
    if not title or _EXCLUDED_TITLE_RE.search(title):
        return False
    return bool(_ENGINEERING_TITLE_RE.search(title) or _APPLE_ROLE_TITLE_RE.search(title))


def skills_filter(job):
    title_text = job.title.lower()
    desc_text = strip_html(job.description).lower()
    text = " ".join(part for part in (title_text, job.location.lower(), desc_text) if part)
    matched = match_keywords(text, SKILL_KEYWORDS)
    if not matched:
        return False

    title_platform_matches = match_keywords(title_text, _PLATFORM_KEYWORDS)
    title_framework_matches = match_keywords(title_text, _APPLE_FRAMEWORK_KEYWORDS)
    title_language_matches = match_keywords(title_text, _LANGUAGE_KEYWORDS)
    title_non_apple_matches = match_keywords(title_text, _NON_APPLE_STACK_KEYWORDS)

    desc_platform_matches = match_keywords(desc_text, _PLATFORM_KEYWORDS)
    desc_framework_matches = match_keywords(desc_text, _APPLE_FRAMEWORK_KEYWORDS)
    desc_language_matches = match_keywords(desc_text, _LANGUAGE_KEYWORDS)

    strong_title_language_matches = match_keywords(title_text, _STRONG_TITLE_LANGUAGE_KEYWORDS)
    title_has_apple_target = bool(
        title_platform_matches or title_framework_matches or strong_title_language_matches
    )

    if title_non_apple_matches and not title_has_apple_target:
        return False

    if title_has_apple_target:
        job.matched_skills = sorted(set(matched))
        return True

    desc_apple_signals = set(
        desc_platform_matches + desc_framework_matches + desc_language_matches
    )
    has_explicit_desc_target = bool(desc_platform_matches or desc_framework_matches)

    if not has_explicit_desc_target:
        return False

    if len(desc_apple_signals) < 2:
        return False

    job.matched_skills = sorted(set(matched))
    return True


def has_remote_evidence(job, text):
    if job.is_remote:
        return True
    if "remote" in job.location.lower():
        return True
    return bool(match_keywords(text, _REMOTE_KEYWORDS))


def has_relocation_evidence(text):
    pattern = "|".join(re.escape(kw) for kw in RELOCATION_KEYWORDS)
    return bool(re.search(pattern, text))


def has_relocation_blocker(text):
    return any(phrase in text for phrase in _RELOCATION_BLOCKERS)


def remote_filter(job):
    text = job_text(job)
    remote_evidence = has_remote_evidence(job, text)
    onsite_conflict = bool(match_keywords(text, _HYBRID_OR_ONSITE_KEYWORDS))

    if not remote_evidence:
        return False

    if onsite_conflict and "remote" not in text:
        return False

    return True


def relocation_filter(job, relocation_regions=None):
    relocation_regions = relocation_regions or DEFAULT_RELOCATION_REGIONS
    region = job.region if job.region != Region.UNKNOWN else classify_region(job)
    if region not in relocation_regions:
        return False

    if job.source.lower() in RELOCATION_GUARANTEED_SOURCES:
        return True

    text = job_text(job)
    if has_relocation_blocker(text):
        return False
    return has_relocation_evidence(text)


def is_israel_job(job):
    loc = job.location.lower()
    for token in IL_LOCATIONS:
        if contains_location_token(loc, token):
            return True
    return False


def opportunity_filter(job, relocation_regions=None):
    if is_israel_job(job):
        return True  # LLM in criteria.md judges office-days requirement
    return remote_filter(job) or relocation_filter(job, relocation_regions)


def contains_location_token(location, token):
    if token in _SHORT_LOCATION_TOKENS:
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", location)
        )
    return token in location


def classify_region(job):
    loc = job.location.lower()
    for token in EU_COUNTRIES | EU_CITIES:
        if contains_location_token(loc, token):
            return Region.EU
    for token in CA_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.CA
    for token in AU_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.AU
    for token in US_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.US
    return Region.UNKNOWN


def apply_region(job):
    job.region = classify_region(job)
    return job


def dedup(jobs):
    seen = set()
    result = []
    for job in jobs:
        key = job.url.rstrip("/").lower()
        alt_key = "{}|{}".format(job.title.lower().strip(), job.company.lower().strip())
        location = collapse_ws(job.location).lower()
        if location:
            alt_key = "{}|{}".format(alt_key, location)
        if key in seen or alt_key in seen:
            continue
        seen.add(key)
        seen.add(alt_key)
        result.append(job)
    return result


def filter_by_age(jobs, max_age_days):
    if max_age_days <= 0:
        return jobs
    cutoff = dt.date.today() - dt.timedelta(days=max_age_days)
    return [job for job in jobs if job.date_posted is None or job.date_posted >= cutoff]


REGION_SORT_ORDER = {
    Region.EU: 0,
    Region.CA: 1,
    Region.AU: 2,
    Region.US: 3,
    Region.UNKNOWN: 4,
}


def sort_jobs(jobs):
    return sorted(jobs, key=lambda job: REGION_SORT_ORDER.get(job.region, 99))


def run_pipeline(jobs, max_age_days=30, relocation_regions=None):
    jobs = [job for job in jobs if india_exclusion_filter(job)]
    jobs = [job for job in jobs if role_filter(job)]
    jobs = [job for job in jobs if skills_filter(job)]
    jobs = [apply_region(job) for job in jobs]
    jobs = [job for job in jobs if opportunity_filter(job, relocation_regions)]
    jobs = filter_by_age(jobs, max_age_days)
    jobs = dedup(jobs)
    jobs = sort_jobs(jobs)
    return jobs


def parse_iso_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    if hasattr(dt.datetime, "fromisoformat"):
        try:
            return dt.datetime.fromisoformat(normalized).date()
        except (TypeError, ValueError):
            pass
    if hasattr(dt.date, "fromisoformat"):
        try:
            return dt.date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except (TypeError, ValueError):
            pass
    return None


def parse_epoch_date(value):
    if value is None or value == "":
        return None
    try:
        return dt.datetime.fromtimestamp(int(value)).date()
    except (TypeError, ValueError, OSError):
        return None


def parse_email_date(value):
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def build_url(url, params=None):
    if not params:
        return url
    query = urllib.parse.urlencode(params, doseq=True)
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return url + separator + query


def response_text(response, raw):
    charset = None
    content_type = response.headers.get("content-type", "")
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).strip("\"'")
    if not charset:
        charset = "utf-8"
    return raw.decode(charset, "replace")


def http_request(url, params=None, method="GET", json_body=None, headers=None):
    request_url = build_url(url, params=params)
    body = None
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 PortableJobScraper/1.0"
        ),
        "Accept": "application/json, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    }
    if headers:
        request_headers.update(headers)
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        request_url,
        data=body,
        headers=request_headers,
        method=method,
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(
        request,
        timeout=HTTP_TIMEOUT_SECONDS,
        context=context,
    ) as response:
        raw = response.read()
        return response.status, response_text(response, raw)


def http_json(url, params=None, method="GET", json_body=None, headers=None):
    status, text = http_request(
        url,
        params=params,
        method=method,
        json_body=json_body,
        headers=headers,
    )
    return status, json.loads(text)


def verbose_source_error(source_name, verbose, exc):
    if verbose:
        if isinstance(exc, urllib.error.HTTPError):
            print("[{}] HTTP {}".format(source_name, exc.code))
        else:
            print("[{}] Error: {}".format(source_name, exc))


class BaseSource(object):
    name = "base"

    def fetch(self, verbose=False):
        raise NotImplementedError


class RemotiveSource(BaseSource):
    name = "remotive"
    API_URL = "https://remotive.com/api/remote-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"limit": "100"})
            if status != 200:
                if verbose:
                    print("[remotive] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            tags = item.get("tags", []) or []
            category = item.get("category", "") or ""
            job_type = item.get("job_type", "") or ""
            description = item.get("description", "") or ""
            extra = " ".join(part for part in [category, job_type] + tags if part)
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("candidate_required_location", "") or "Remote",
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_iso_date(item.get("publication_date")),
                    description="{} {}".format(description, extra).strip(),
                    is_remote=True,
                )
            )

        if verbose:
            print("[remotive] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class RemoteOKSource(BaseSource):
    name = "remoteok"
    API_URL = "https://remoteok.com/api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL)
            if status != 200:
                if verbose:
                    print("[remoteok] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        items = data[1:] if isinstance(data, list) and len(data) > 1 else []
        for item in items:
            if not isinstance(item, dict):
                continue
            posted = parse_epoch_date(item.get("epoch")) or parse_iso_date(item.get("date"))
            url = item.get("url", "") or ""
            if url and not url.startswith("http"):
                url = "https://remoteok.com{}".format(url)
            tags = item.get("tags", []) or []
            jobs.append(
                Job(
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=item.get("location", "") or "Remote",
                    url=url,
                    source=self.name,
                    date_posted=posted,
                    description="{} {}".format(item.get("description", "") or "", " ".join(tags)),
                    is_remote=True,
                )
            )

        if verbose:
            print("[remoteok] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class JobicySource(BaseSource):
    name = "jobicy"
    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"count": "50"})
            if status != 200:
                if verbose:
                    print("[jobicy] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            jobs.append(
                Job(
                    title=item.get("jobTitle", ""),
                    company=item.get("companyName", ""),
                    location=item.get("jobGeo", "") or "Remote",
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_iso_date(item.get("pubDate")),
                    description=item.get("jobDescription", ""),
                    is_remote=True,
                )
            )

        if verbose:
            print("[jobicy] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class ArbeitnowSource(BaseSource):
    name = "arbeitnow"
    API_URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"visa_sponsorship": "true"})
            if status != 200:
                if verbose:
                    print("[arbeitnow] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("data", []):
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("location", ""),
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_epoch_date(item.get("created_at")),
                    description=item.get("description", ""),
                    is_remote=bool(item.get("remote", False)),
                )
            )

        if verbose:
            print("[arbeitnow] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class TheMuseSource(BaseSource):
    name = "themuse"
    API_URL = "https://www.themuse.com/api/public/jobs"
    MAX_PAGES = 5

    def fetch(self, verbose=False):
        jobs = []
        for page in range(self.MAX_PAGES):
            try:
                status, data = http_json(
                    self.API_URL,
                    params={"page": str(page), "location": "Remote"},
                )
                if status != 200:
                    if verbose:
                        print("[themuse] HTTP {} for page={}".format(status, page))
                    continue
            except Exception as exc:
                verbose_source_error(self.name, verbose, exc)
                return jobs

            jobs.extend(self.parse_items(data.get("results", [])))

            try:
                page_count = int(data.get("page_count") or 0)
            except (TypeError, ValueError):
                page_count = 0
            if page + 1 >= page_count:
                break

        if verbose:
            print("[themuse] Fetched {} raw jobs".format(len(jobs)))
        return jobs

    def parse_items(self, items):
        jobs = []
        for item in items:
            locations = []
            for loc in item.get("locations", []) or []:
                if isinstance(loc, dict) and loc.get("name"):
                    locations.append(loc.get("name", ""))
            categories = []
            for cat in item.get("categories", []) or []:
                if isinstance(cat, dict) and cat.get("name"):
                    categories.append(cat.get("name", ""))
            levels = []
            for level in item.get("levels", []) or []:
                if isinstance(level, dict) and level.get("name"):
                    levels.append(level.get("name", ""))

            company = item.get("company") or {}
            refs = item.get("refs") or {}
            jobs.append(
                Job(
                    title=item.get("name", ""),
                    company=company.get("name", "") if isinstance(company, dict) else "",
                    location=", ".join(locations) or "Remote",
                    url=refs.get("landing_page", "") if isinstance(refs, dict) else "",
                    source=self.name,
                    date_posted=parse_iso_date(item.get("publication_date")),
                    description=" ".join(
                        part
                        for part in [
                            item.get("contents", ""),
                            " ".join(categories),
                            " ".join(levels),
                        ]
                        if part
                    ),
                    is_remote=True,
                )
            )
        return jobs


class HimalayasSource(BaseSource):
    name = "himalayas"
    API_URL = "https://himalayas.app/jobs/api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"limit": "50"})
            if status != 200:
                if verbose:
                    print("[himalayas] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            categories = item.get("categories", []) or []
            location_restrictions = item.get("locationRestrictions", []) or []
            if location_restrictions:
                location = ", ".join(str(x) for x in location_restrictions)
            else:
                location = "Remote"
            desc = item.get("description", "") or item.get("excerpt", "") or ""
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=item.get("applicationLink", "") or item.get("guid", "") or "",
                    source=self.name,
                    date_posted=parse_iso_date(item.get("pubDate")),
                    description="{} {}".format(desc, " ".join(str(x) for x in categories)),
                    is_remote=True,
                )
            )

        if verbose:
            print("[himalayas] Fetched {} raw jobs".format(len(jobs)))
        return jobs


_LOCATION_RE = re.compile(r"(?:^|\n)\s*Location:\s*(.+)")


def parse_rss_date(text):
    return parse_email_date(text)


def split_title_company(title):
    title = collapse_ws(title)
    if " at " in title:
        job_title, company = title.rsplit(" at ", 1)
        return job_title.strip(), company.strip()
    if ": " in title:
        company, job_title = title.split(": ", 1)
        return job_title.strip(), company.strip()
    return title.strip(), ""


def extract_location(description, default="Remote"):
    text = strip_html(description)
    match = _LOCATION_RE.search(text)
    if match:
        return match.group(1).splitlines()[0].strip() or default
    guessed = guess_location_from_text(text)
    return guessed or default


def parse_rss_jobs(xml_text, source, default_location="Remote", is_remote=True):
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        title, company = split_title_company(raw_title)
        description = item.findtext("description") or ""
        categories = []
        for element in item.findall("category"):
            if element.text:
                categories.append(element.text.strip())
        jobs.append(
            Job(
                title=title,
                company=company,
                location=extract_location(description, default=default_location),
                url=(item.findtext("link") or "").strip(),
                source=source,
                date_posted=parse_rss_date(item.findtext("pubDate") or ""),
                description="{} {}".format(description, " ".join(categories)).strip(),
                is_remote=is_remote,
            )
        )
    return jobs


class WeWorkRemotelySource(BaseSource):
    name = "weworkremotely"
    FEEDS = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    ]

    def fetch(self, verbose=False):
        jobs = []
        for feed_url in self.FEEDS:
            try:
                status, text = http_request(feed_url)
                if status != 200:
                    if verbose:
                        print("[weworkremotely] HTTP {} for {}".format(status, feed_url))
                    continue
                root = ET.fromstring(text)
            except Exception as exc:
                if verbose:
                    print("[weworkremotely] Error fetching {}: {}".format(feed_url, exc))
                continue

            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = item.findtext("description") or ""
                pub_date = item.findtext("pubDate") or ""
                region = (item.findtext("region") or "").strip()
                country = (item.findtext("country") or "").strip()
                company = ""
                if ": " in title:
                    company, title = title.split(": ", 1)
                jobs.append(
                    Job(
                        title=title,
                        company=company,
                        location=country or region or "Remote",
                        url=link,
                        source=self.name,
                        date_posted=parse_email_date(pub_date),
                        description=desc,
                        is_remote=True,
                    )
                )

        if verbose:
            print("[weworkremotely] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class RemoteFirstJobsSource(BaseSource):
    name = "remotefirstjobs"
    RSS_URL = "https://remotefirstjobs.com/rss/jobs.rss"

    def fetch(self, verbose=False):
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[remotefirstjobs] HTTP {}".format(status))
                return []
            jobs = parse_rss_jobs(text, source=self.name, default_location="Remote")
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return []
        if verbose:
            print("[remotefirstjobs] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class RemoteVibeSource(BaseSource):
    name = "remotevibe"
    RSS_URL = "https://remotevibecodingjobs.com/feed.xml"

    def fetch(self, verbose=False):
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[remotevibe] HTTP {}".format(status))
                return []
            jobs = parse_rss_jobs(text, source=self.name, default_location="Remote")
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return []
        if verbose:
            print("[remotevibe] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class SwissDevJobsSource(BaseSource):
    name = "swissdevjobs"
    RSS_URL = "https://swissdevjobs.ch/rss"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[swissdevjobs] HTTP {}".format(status))
                return jobs
            root = ET.fromstring(text)
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in root.iter("item"):
            title_raw = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            pub_date = item.findtext("pubDate") or ""
            company = ""
            title = title_raw
            if " @ " in title_raw:
                title, rest = title_raw.split(" @ ", 1)
                parts = rest.split(" - ")
                company = parts[0].strip()
            jobs.append(
                Job(
                    title=title.strip(),
                    company=company,
                    location="Switzerland",
                    url=link,
                    source=self.name,
                    date_posted=parse_email_date(pub_date),
                    description=desc,
                )
            )

        if verbose:
            print("[swissdevjobs] Fetched {} raw jobs".format(len(jobs)))
        return jobs


MOBILECAREER_JOB_OBJECT_RE = re.compile(
    r'\{\\?"id\\?":\\?"[^"\\]+\\?",\\?"applyType\\?":.*?\\?"paymentStatus\\?":\\?"[^"\\]+\\?"\}',
    re.DOTALL,
)


def decode_mobilecareer_job_object(raw):
    try:
        if '\\"' in raw:
            return json.loads(json.loads('"{}"'.format(raw)))
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def mobilecareer_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("$D"):
        text = text[2:]
    return parse_iso_date(text)


def mobilecareer_location(item):
    raw_parts = item.get("location") or []
    if not isinstance(raw_parts, list):
        raw_parts = [raw_parts]

    parts = []
    seen = set()
    for raw in raw_parts:
        part = str(raw or "").strip(" ,")
        if not part or part.lower() in seen:
            continue
        seen.add(part.lower())
        parts.append(part)

    location_type = str(item.get("locationType") or "").strip().title()
    prefix = location_type or "Remote"
    if not parts:
        return prefix
    return "{} - {}".format(prefix, ", ".join(parts))


def mobilecareer_description(item):
    skills = item.get("skills") or []
    if isinstance(skills, list):
        skills_text = " ".join(str(skill) for skill in skills if skill)
    else:
        skills_text = str(skills)

    parts = [
        item.get("role"),
        item.get("employmentType"),
        item.get("seniority"),
        item.get("companyTagline"),
        skills_text,
    ]
    salary_min = item.get("salaryMin")
    salary_max = item.get("salaryMax")
    if salary_min or salary_max:
        parts.append("Salary {}-{}".format(salary_min or "", salary_max or ""))
    return " ".join(str(part) for part in parts if part).strip()


class MobileCareerSource(BaseSource):
    name = "mobile.career"
    IOS_URL = "https://mobile.career/ios-developer-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, text = http_request(self.IOS_URL)
            if status != 200:
                if verbose:
                    print("[mobile.career] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        seen = set()
        for match in MOBILECAREER_JOB_OBJECT_RE.finditer(text):
            item = decode_mobilecareer_job_object(match.group(0))
            if not item:
                continue

            job_id = str(item.get("id") or "")
            slug = str(item.get("slug") or "")
            key = job_id or slug
            if not key or key in seen:
                continue
            seen.add(key)

            url = str(item.get("applyTo") or "")
            if not url and slug:
                url = "https://mobile.career/jobs/{}".format(slug)

            location = mobilecareer_location(item)
            location_type = str(item.get("locationType") or "").upper()
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=url,
                    source=self.name,
                    date_posted=mobilecareer_date(
                        item.get("publishedWebAt") or item.get("createdAt")
                    ),
                    description=mobilecareer_description(item),
                    is_remote=location_type == "REMOTE",
                )
            )

        if verbose:
            print("[mobile.career] Fetched {} raw jobs".format(len(jobs)))
        return jobs


JOBSCROLLER_CARD_RE = re.compile(
    r'<a class="block bg-slate-900 border border-slate-800 rounded-xl p-5[^"]*" href="(/jobs/\d+)">(.*?)</a>',
    re.DOTALL,
)
JOBSCROLLER_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
JOBSCROLLER_META_RE = re.compile(
    r'<div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-slate-500">(.*?)</div>',
    re.DOTALL,
)
JOBSCROLLER_TAGS_RE = re.compile(
    r'<div class="hidden sm:flex flex-wrap gap-1 justify-end max-w-\[200px\] shrink-0">(.*?)</div>',
    re.DOTALL,
)
JOBSCROLLER_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.DOTALL)
JOBSCROLLER_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*(m|h|d|w|mo|y)\s+ago", re.IGNORECASE)
JOBSCROLLER_SALARY_RE = re.compile(r"[$€£]\S+")


def clean_fragment_text(value):
    return html.unescape(strip_html(value)).strip()


def jobscroller_relative_date(value):
    text = value.strip().lower()
    match = JOBSCROLLER_RELATIVE_DATE_RE.search(text)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit in ("m", "h"):
        days = 0
    elif unit == "d":
        days = amount
    elif unit == "w":
        days = amount * 7
    elif unit == "mo":
        days = amount * 30
    else:
        days = amount * 365
    return dt.date.today() - dt.timedelta(days=days)


def jobscroller_card_values(card_html):
    paragraphs = [clean_fragment_text(value) for value in JOBSCROLLER_PARAGRAPH_RE.findall(card_html)]
    company = paragraphs[0] if len(paragraphs) >= 1 else ""
    title = paragraphs[1] if len(paragraphs) >= 2 else ""

    meta_values = []
    meta_match = JOBSCROLLER_META_RE.search(card_html)
    if meta_match:
        meta_values = [
            clean_fragment_text(value)
            for value in JOBSCROLLER_SPAN_RE.findall(meta_match.group(1))
        ]
        meta_values = [value for value in meta_values if value and value != "·"]

    location = meta_values[0] if meta_values else ""
    relative_date = ""
    for value in meta_values[1:]:
        if "ago" in value.lower():
            relative_date = value
            break

    tags = []
    tag_match = JOBSCROLLER_TAGS_RE.search(card_html)
    if tag_match:
        tags = [
            clean_fragment_text(value)
            for value in JOBSCROLLER_SPAN_RE.findall(tag_match.group(1))
        ]
        tags = [tag for tag in tags if tag]

    description_parts = tags + [
        value for value in meta_values if not JOBSCROLLER_SALARY_RE.search(value)
    ]
    description = " ".join(part for part in description_parts if part)
    return company, title, location, relative_date, description


class JobScrollerSource(BaseSource):
    name = "jobscroller"
    ROLE_URLS = (
        "https://www.jobscroller.net/roles/swift",
        "https://www.jobscroller.net/roles/objective-c",
    )

    def fetch(self, verbose=False):
        jobs = []
        seen = set()
        for role_url in self.ROLE_URLS:
            try:
                status, text = http_request(role_url)
                if status != 200:
                    if verbose:
                        print("[jobscroller] HTTP {} for {}".format(status, role_url))
                    continue
            except Exception as exc:
                if verbose:
                    print("[jobscroller] Error fetching {}: {}".format(role_url, exc))
                continue

            for match in JOBSCROLLER_CARD_RE.finditer(text):
                href = match.group(1)
                if href in seen:
                    continue
                seen.add(href)

                company, title, location, relative_date, description = jobscroller_card_values(
                    match.group(2)
                )
                if not title:
                    continue

                jobs.append(
                    Job(
                        title=title,
                        company=company,
                        location=location,
                        url="https://www.jobscroller.net{}".format(href),
                        source=self.name,
                        date_posted=jobscroller_relative_date(relative_date),
                        description=description,
                        is_remote="remote" in location.lower(),
                    )
                )

        if verbose:
            print("[jobscroller] Fetched {} raw jobs".format(len(jobs)))
        return jobs


COUNTRY_NAMES = {
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CL": "Chile",
    "CO": "Colombia",
    "DE": "Germany",
    "DK": "Denmark",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IT": "Italy",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "PL": "Poland",
    "PT": "Portugal",
    "SE": "Sweden",
    "US": "United States",
}


class ArcSource(BaseSource):
    name = "arc"
    BASE_URL = "https://arc.dev"
    PATHS = ("mobile-ios", "swift")
    NEXT_RE = re.compile(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
        re.DOTALL | re.IGNORECASE,
    )

    def fetch(self, verbose=False):
        jobs = []
        seen = set()
        for path in self.PATHS:
            url = "{}/remote-jobs/{}".format(self.BASE_URL, path)
            try:
                status, text = http_request(url)
                if status != 200:
                    if verbose:
                        print("[arc] HTTP {} for {}".format(status, path))
                    continue
            except Exception as exc:
                if verbose:
                    print("[arc] Error fetching {}: {}".format(path, exc))
                continue

            for job in self.parse_html(text):
                key = job.url or "{}|{}".format(job.title, job.company)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)

        if verbose:
            print("[arc] Fetched {} raw jobs".format(len(jobs)))
        return jobs

    def parse_html(self, html_text):
        data = self.next_data(html_text)
        if data is None:
            return []

        jobs = []
        seen = set()
        for item in self.walk_jobs(data):
            key = str(item.get("randomKey") or item.get("urlString") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            jobs.append(self.to_job(item))
        return jobs

    def next_data(self, html_text):
        match = self.NEXT_RE.search(html_text)
        if not match:
            return None
        raw = match.group(1).strip()
        for candidate in (raw, html.unescape(raw)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        return None

    def walk_jobs(self, obj):
        found = []
        if isinstance(obj, dict):
            if set(["randomKey", "title", "company", "urlString"]) <= set(obj.keys()):
                found.append(obj)
            for value in obj.values():
                found.extend(self.walk_jobs(value))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(self.walk_jobs(item))
        return found

    def to_job(self, item):
        company = item.get("company") or {}
        category_names = []
        for cat in item.get("categories", []) or []:
            if isinstance(cat, dict) and cat.get("name"):
                category_names.append(cat.get("name", ""))
        levels = item.get("experienceLevels", []) or []
        countries = item.get("requiredCountries", []) or []
        location = self.country_location(countries)
        return Job(
            title=item.get("title", ""),
            company=company.get("name", "") if isinstance(company, dict) else "",
            location=location,
            url=self.job_url(item),
            source=self.name,
            date_posted=parse_epoch_date(item.get("postedAt")),
            description=" ".join(
                part
                for part in [
                    item.get("positionType", ""),
                    item.get("jobType", ""),
                    " ".join(str(x) for x in levels),
                    " ".join(category_names),
                ]
                if part
            ),
            is_remote=True,
        )

    def job_url(self, item):
        url_string = str(item.get("urlString") or "").strip("/")
        random_key = str(item.get("randomKey") or "")
        if not url_string:
            return "{}/remote-jobs".format(self.BASE_URL)
        slug = url_string
        if random_key and not slug.endswith("-{}".format(random_key)):
            slug = "{}-{}".format(slug, random_key)
        route = "details" if self.is_arc_exclusive(item) else "j"
        return "{}/remote-jobs/{}/{}".format(self.BASE_URL, route, slug)

    def is_arc_exclusive(self, item):
        return "jobRole" in item or "availableHoursPerWeek" in item

    def country_location(self, countries):
        if not countries:
            return "Remote"
        names = [COUNTRY_NAMES.get(code, code) for code in countries]
        return "Remote - " + ", ".join(names)


class WorkingNomadsSource(BaseSource):
    name = "workingnomads"
    BASE_URL = "https://www.workingnomads.com"
    API_URL = BASE_URL + "/jobsapi/_search"

    def payload(self):
        query = (
            '"ios" OR "ipados" OR "macos" OR "iphone" OR "ipad" OR '
            '"swiftui" OR "uikit" OR "appkit" OR "objective-c" OR '
            '("swift" AND ("ios" OR "macos" OR "swiftui" OR "uikit" OR "xcode"))'
        )
        return {
            "track_total_hits": True,
            "from": 0,
            "size": 100,
            "_source": [
                "company",
                "locations",
                "title",
                "pub_date",
                "description",
                "slug",
                "apply_url",
                "tags",
                "position_type",
                "experience_level",
            ],
            "sort": [{"pub_date": {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": {
                        "query_string": {
                            "query": query,
                            "fields": ["title^2", "description", "company"],
                        }
                    }
                }
            },
            "min_score": 2,
        }

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(
                self.API_URL,
                method="POST",
                json_body=self.payload(),
            )
            if status != 200:
                if verbose:
                    print("[workingnomads] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        hits = data.get("hits", {}).get("hits", [])
        for item in hits:
            source = item.get("_source", {}) if isinstance(item, dict) else {}
            slug = source.get("slug", "") or ""
            if slug:
                url = "{}/jobs/{}".format(self.BASE_URL, slug)
            else:
                url = source.get("apply_url", "") or ""
            tags = source.get("tags", []) or []
            locations = source.get("locations", []) or []
            extra = " ".join(
                part
                for part in [
                    source.get("position_type", ""),
                    source.get("experience_level", ""),
                    " ".join(str(x) for x in tags),
                ]
                if part
            )
            jobs.append(
                Job(
                    title=source.get("title", ""),
                    company=source.get("company", ""),
                    location=", ".join(str(x) for x in locations) or "Remote",
                    url=url,
                    source=self.name,
                    date_posted=parse_iso_date(source.get("pub_date")),
                    description="{} {}".format(source.get("description", ""), extra).strip(),
                    is_remote=True,
                )
            )

        if verbose:
            print("[workingnomads] Fetched {} raw jobs".format(len(jobs)))
        return jobs


def guess_location_from_text(text):
    lower = str(text or "").lower()
    token_sets = [
        (Region.EU, EU_COUNTRIES | EU_CITIES),
        (Region.CA, CA_LOCATIONS),
        (Region.AU, AU_LOCATIONS),
        (Region.US, US_LOCATIONS),
    ]
    for _region, tokens in token_sets:
        for token in sorted(tokens, key=len, reverse=True):
            if contains_location_token(lower, token):
                return token.title()
    return ""


_A_TAG_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)


def extract_attr(attrs, name):
    pattern = re.compile(
        r"\b" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(attrs or "")
    return html.unescape(match.group(2)) if match else ""


def parse_link_jobs(html_text, base_url, href_fragments, source_name, default_remote=False):
    jobs = []
    seen = set()
    for match in _A_TAG_RE.finditer(html_text):
        href = extract_attr(match.group("attrs"), "href")
        if not href or not any(fragment in href for fragment in href_fragments):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        text = collapse_ws(strip_html(match.group("body")))
        if not text or len(text) < 3:
            continue
        title, company = split_title_company(text)
        title = title[:140]
        location = guess_location_from_text(text)
        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                url=url,
                source=source_name,
                description=text,
                is_remote=default_remote,
            )
        )
    return jobs


class RelocateMeSource(BaseSource):
    name = "relocate.me"
    BASE_URL = "https://relocate.me"
    SEARCH_URL = BASE_URL + "/search"
    QUERIES = ["ios", "macos", "swiftui"]

    def fetch(self, verbose=False):
        jobs = []
        for query in self.QUERIES:
            try:
                status, text = http_request(self.SEARCH_URL, params={"q": query})
                if status != 200:
                    if verbose:
                        print("[relocate.me] HTTP {} for query={!r}".format(status, query))
                    continue
                jobs.extend(
                    parse_link_jobs(
                        text,
                        self.BASE_URL,
                        ["/job/", "/jobs/", "/vacancy/"],
                        self.name,
                        default_remote=False,
                    )
                )
            except Exception as exc:
                if verbose:
                    print("[relocate.me] Error for query={!r}: {}".format(query, exc))
                continue

        jobs = dedup(jobs)
        if verbose:
            print("[relocate.me] Fetched {} raw jobs".format(len(jobs)))
        return jobs


class SecretTelAvivSource(BaseSource):
    # Cloudflare-fronted: returns 403 to stdlib urllib from datacenter IPs, so we
    # drive a real headless Chromium via Playwright (verified to get HTTP 200).
    # Skips automatically if Playwright/Chromium isn't installed.
    name = "secrettelaviv"
    optional_dependency = "playwright"
    BASE_URL = "https://jobs.secrettelaviv.com"
    SEARCH_URL = BASE_URL + "/list/find/"
    QUERIES = ["ios", "swift", "macos", "mobile developer"]
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def fetch(self, verbose=False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if verbose:
                print("[secrettelaviv] Skipped: Playwright not installed")
            return []

        jobs = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT, locale="en-US")
                try:
                    for query in self.QUERIES:
                        url = build_url(self.SEARCH_URL, params={"q": query})
                        page = context.new_page()
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=45000)
                            page.wait_for_timeout(3000)  # let any CF check settle
                            html = page.content()
                            jobs.extend(
                                parse_link_jobs(
                                    html,
                                    self.BASE_URL,
                                    ["/job/"],
                                    self.name,
                                    default_remote=False,
                                )
                            )
                        except Exception as exc:
                            if verbose:
                                print("[secrettelaviv] Error for query={!r}: {}".format(query, exc))
                        finally:
                            page.close()
                finally:
                    browser.close()
        except Exception as exc:
            if verbose:
                print("[secrettelaviv] Playwright error: {}".format(exc))
            return dedup(jobs)

        jobs = dedup(jobs)
        if verbose:
            print("[secrettelaviv] Fetched {} raw jobs".format(len(jobs)))
        return jobs


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


class JobSpySource(BaseSource):
    name = "jobspy"
    optional_dependency = "python-jobspy"

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


class LinkedInGlobalSource(BaseSource):
    name = "linkedin-global"
    optional_dependency = "python-jobspy"

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


class LinkedInIsraelSource(BaseSource):
    name = "linkedin-israel"
    optional_dependency = "python-jobspy"

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


ALL_SOURCES = {
    "jobspy": JobSpySource,
    "linkedin-global": LinkedInGlobalSource,
    "linkedin-israel": LinkedInIsraelSource,
    "arc": ArcSource,
    "mobile.career": MobileCareerSource,
    "jobscroller": JobScrollerSource,
    "arbeitnow": ArbeitnowSource,
    "jobicy": JobicySource,
    "remoteok": RemoteOKSource,
    "weworkremotely": WeWorkRemotelySource,
    "remotefirstjobs": RemoteFirstJobsSource,
    "remotevibe": RemoteVibeSource,
    "himalayas": HimalayasSource,
    "remotive": RemotiveSource,
    "workingnomads": WorkingNomadsSource,
    "themuse": TheMuseSource,
    "swissdevjobs": SwissDevJobsSource,
    "relocate.me": RelocateMeSource,
    "secrettelaviv": SecretTelAvivSource,
}


SOURCE_DESCRIPTIONS = {
    "jobspy": "Optional JobSpy search. Skips automatically unless python-jobspy is installed.",
    "linkedin-global": "LinkedIn iOS/Swift jobs globally (Remote + key relocation countries) via JobSpy. Skips unless python-jobspy is installed.",
    "linkedin-israel": "LinkedIn iOS/Swift jobs in Israel via JobSpy. Skips unless python-jobspy is installed.",
    "arc": "Arc remote iOS/Swift pages.",
    "mobile.career": "Mobile.Career iOS jobs with direct company/ATS apply links.",
    "jobscroller": "JobScroller Swift/Objective-C company-career-page listings.",
    "arbeitnow": "Arbeitnow visa-sponsorship API.",
    "jobicy": "Jobicy remote jobs API.",
    "remoteok": "RemoteOK API.",
    "weworkremotely": "We Work Remotely RSS feeds.",
    "remotefirstjobs": "Remote First Jobs RSS.",
    "remotevibe": "Remote Vibe RSS.",
    "himalayas": "Himalayas remote jobs API.",
    "remotive": "Remotive remote jobs API.",
    "workingnomads": "Working Nomads search API.",
    "themuse": "The Muse remote jobs API.",
    "swissdevjobs": "SwissDevJobs RSS.",
    "relocate.me": "Relocate.me search pages, best effort with stdlib HTML parsing.",
    "secrettelaviv": "Secret Tel Aviv (Cloudflare-fronted): Israel-focused English listings via Playwright/Chromium. Skips unless Playwright is installed.",
}


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


def job_to_dict(job):
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "is_remote": job.is_remote,
        "region": job.region.value,
        "matched_skills": job.matched_skills,
    }


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


def choose_sources_interactively(current):
    print("")
    names = list(ALL_SOURCES.keys())
    print("Available sources:")
    for index, name in enumerate(names, start=1):
        print("  {:2}. {:18} {}".format(index, name, SOURCE_DESCRIPTIONS.get(name, "")))
    print("")
    print("Enter comma-separated source names, numbers, or 'all'.")
    print("Current: {}".format(source_names_for_display(current)))
    raw = input("Sources> ").strip()
    if not raw:
        return current
    if raw.lower() == "all":
        return None

    selected = []
    invalid = []
    for part in [item.strip().lower() for item in raw.split(",") if item.strip()]:
        if part.isdigit():
            index = int(part) - 1
            if 0 <= index < len(names):
                selected.append(names[index])
            else:
                invalid.append(part)
        elif part in ALL_SOURCES:
            selected.append(part)
        else:
            invalid.append(part)
    if invalid:
        print("Ignored unknown selections: {}".format(", ".join(invalid)))
    return selected or current


def choose_regions_interactively(current):
    print("")
    print("Available regions: eu, ca, au, us")
    print("Current: {}".format(regions_for_display(current)))
    raw = input("Regions> ").strip()
    if not raw:
        return current
    return parse_regions(raw)


def choose_max_age_interactively(current):
    print("")
    raw = input("Max age in days (0 keeps all, current {}): ".format(current)).strip()
    if not raw:
        return current
    try:
        value = int(raw)
    except ValueError:
        print("Invalid number; keeping current value.")
        return current
    return max(0, value)


def interactive_menu(initial_args=None):
    source_names = None
    regions = set(DEFAULT_RELOCATION_REGIONS)
    max_age = 30
    as_json = False
    verbose = False

    if initial_args is not None:
        source_names = parse_sources(initial_args.sources)
        regions = parse_regions(initial_args.relocation_region)
        max_age = initial_args.max_age
        as_json = initial_args.as_json
        verbose = initial_args.verbose

    while True:
        print("")
        print("Portable iOS/macOS Job Scraper")
        print("1. Run scraper")
        print("2. Choose sources        [{}]".format(source_names_for_display(source_names)))
        print("3. Choose regions        [{}]".format(regions_for_display(regions)))
        print("4. Set max job age       [{} days]".format(max_age))
        print("5. Toggle JSON output    [{}]".format("on" if as_json else "off"))
        print("6. Toggle verbose output [{}]".format("on" if verbose else "off"))
        print("7. List sources")
        print("8. Quit")
        choice = input("> ").strip().lower()

        if choice in ("", "1", "run", "r"):
            return run_scraper(
                source_names=source_names,
                relocation_regions=regions,
                max_age=max_age,
                as_json=as_json,
                verbose=verbose,
            )
        if choice in ("2", "sources", "s"):
            source_names = choose_sources_interactively(source_names)
        elif choice in ("3", "regions", "region"):
            regions = choose_regions_interactively(regions)
        elif choice in ("4", "age", "max-age"):
            max_age = choose_max_age_interactively(max_age)
        elif choice in ("5", "json", "j"):
            as_json = not as_json
        elif choice in ("6", "verbose", "v"):
            verbose = not verbose
        elif choice in ("7", "list", "l"):
            print_sources()
        elif choice in ("8", "quit", "q", "exit"):
            return 0
        else:
            print("Unknown option.")


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Scrape iOS/macOS remote jobs globally and relocation/visa jobs "
            "in selected regions. With no arguments, opens an interactive menu."
        )
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources or 'all'. Use --list-sources to see names.",
    )
    parser.add_argument(
        "--relocation-region",
        "--region",
        dest="relocation_region",
        default="eu,ca,us",
        help="Comma-separated relocation regions: eu, ca, au, us. Remote jobs are global.",
    )
    parser.add_argument(
        "--max-age",
        default=30,
        type=int,
        help="Max job age in days. Use 0 to keep all dates. Default: 30.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output JSON instead of plain text.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug information from each source.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print available source names and exit.",
    )
    parser.add_argument(
        "--menu",
        action="store_true",
        help="Open the interactive menu even when other arguments are present.",
    )
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_sources:
        print_sources()
        return 0

    if args.menu or (not argv and sys.stdin.isatty()):
        return interactive_menu(args)

    return run_scraper(
        source_names=parse_sources(args.sources),
        relocation_regions=parse_regions(args.relocation_region),
        max_age=args.max_age,
        as_json=args.as_json,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
