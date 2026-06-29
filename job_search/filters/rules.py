"""Pure filter functions: role/skill/remote/relocation rules, dedup, age, sort."""
import datetime as dt
import re

from ..location.classify import classify_region, is_israel_job
from ..models import Region
from ..text import collapse_ws, strip_html
from .keywords import SKILL_KEYWORDS, match_keywords

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


def opportunity_filter(job, relocation_regions=None):
    if is_israel_job(job):
        return True  # LLM in criteria.md judges office-days requirement
    return remote_filter(job) or relocation_filter(job, relocation_regions)


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
