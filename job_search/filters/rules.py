"""Pure filter functions: role/skill/remote/relocation rules, dedup, age, sort."""
import datetime as dt
import re

from ..identity import job_identity_keys
from ..location.classify import classify_region, is_eu_member_job, is_israel_job
from ..models import Region, merge_jobs
from ..text import strip_html
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
        # Spelled out because matching is token-aware now: a keyword no longer
        # reaches inside a longer word the way substring matching did, so
        # "work remotely" and "fully asynchronous" need their own entries.
        "remotely",
        "work from home",
        "work-from-home",
        "distributed",
        "async",
        "asynchronous",
        "anywhere",
        "home office",
    ]
)

# Phrases that *deny* remote work. They contain the evidence keyword itself
# ("remote work is not available" contains "remote"), so without neutralizing
# them first a denial registers as a positive remote signal — the same trap
# has_relocation_evidence already sidesteps for sponsorship blockers
# (audit finding 10).
_REMOTE_NEGATIONS = set(
    [
        "remote work is not available",
        "remote work is not an option",
        "remote work is not possible",
        "remote work is not offered",
        "remote work is unavailable",
        "remote work not available",
        "remote is not available",
        "remote is not an option",
        "no remote work",
        "no remote option",
        "no remote positions",
        "no remote",
        "not a remote role",
        "not a remote position",
        "not a remote job",
        "this is not a remote",
        "not remote",
        "does not offer remote",
        "do not offer remote",
        "we don't offer remote",
        "no fully remote",
        "fully remote is not",
        "not open to remote",
        "not available for remote",
        "remote work is not",
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
        "locals only",
        "local candidates only",
        "eu citizens only",
        "eu residents only",
        "european union citizens only",
        "european union residents only",
        "must already be authorized to work",
        "must currently be authorized to work",
        "must be legally authorized to work",
        "must have existing work authorization",
    ]
)

_INDIA_PLACE = (
    r"(?:india|bengaluru|bangalore|mumbai|delhi|new delhi|hyderabad|"
    r"pune|chennai|kolkata|gurgaon|gurugram|noida)"
)

_INDIA_LOCATION_RE = re.compile(
    r"(?<![a-z])" + _INDIA_PLACE + r"(?![a-z])",
    re.IGNORECASE,
)

# Description wording that ties the *candidate* to India, as opposed to merely
# mentioning an Indian office or offshore team. Scanning the whole description
# for a bare place name (the pre-2026-07-25 behavior) dropped Berlin roles that
# happened to name a Bangalore team — a silent false negative that never showed
# up in any count. See audit finding 10.
_INDIA_RESTRICTION_RE = re.compile(
    r"(?:based (?:in|out of)|located (?:in|at)|residing in|reside in|resident of|"
    r"work(?:ing)? from|work(?:ing)? in|relocat(?:e|ing) to|"
    r"candidates? (?:in|from|based in)|applicants? (?:in|from|based in)|"
    r"open to candidates in|hiring in|"
    r"(?:role|position|job) is (?:based )?in)"
    r"\s+(?:the\s+)?" + _INDIA_PLACE + r"(?![a-z])"
    r"|(?<![a-z])" + _INDIA_PLACE + r"\s*(?:[-–,]\s*)?(?:based|only)(?![a-z])",
    re.IGNORECASE,
)

# ...but "based in" and "located in" describe *companies* at least as often as
# candidates ("Our engineering hub is located in Bangalore"), and "India-based"
# modifies a company noun just as readily ("our India-based team"). Both drop a
# Berlin posting — the same silent false negative this filter exists to close,
# needing only one more sentence of prose. So a match is vetoed when a
# company-thing noun sits next to it: before the phrase for the verb forms,
# after it for the "<place>-based <noun>" form.
_COMPANY_SUBJECT = (
    r"(?:offices?|teams?|hubs?|cent(?:er|re)s?|entit(?:y|ies)|subsidiar(?:y|ies)|"
    r"branch(?:es)?|colleagues|staff|presence|headquarters|hq|sites?|studios?|"
    r"engineers?|developers?|counterparts?|partners?|operations?|division)"
)
# Within the ~60 characters before the match ("Our platform team is based in …").
_COMPANY_SUBJECT_BEFORE_RE = re.compile(
    _COMPANY_SUBJECT + r"\b(?:\W+\w+){0,3}\W*$", re.IGNORECASE
)
# Or directly after it ("India-based team", "Bangalore-based subsidiary").
_COMPANY_SUBJECT_AFTER_RE = re.compile(r"^\W*" + _COMPANY_SUBJECT + r"\b", re.IGNORECASE)


def _india_candidate_restriction(text) -> bool:
    """True when the description restricts the CANDIDATE to India."""
    for match in _INDIA_RESTRICTION_RE.finditer(text):
        before = text[max(0, match.start() - 60):match.start()]
        after = text[match.end():match.end() + 40]
        if _COMPANY_SUBJECT_BEFORE_RE.search(before) or _COMPANY_SUBJECT_AFTER_RE.search(after):
            continue  # describes where the company is, not where the hire must be
        return True
    return False

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
    """Keep the job unless it is actually an India-based posting.

    The place name has to appear where it means the *job* is in India — the
    title or the location field — or the description has to restrict the
    candidate to India explicitly. A description that merely mentions an Indian
    office, team, or entity no longer discards the posting.
    """
    placement = " ".join(part for part in (job.title, job.location) if part).lower()
    if _INDIA_LOCATION_RE.search(placement):
        return False
    return not _india_candidate_restriction(strip_html(job.description).lower())


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


def neutralize_remote_negations(text):
    """Blank out phrases that deny remote work, so they can't read as evidence.

    Longest phrase first, so "no remote work" is consumed whole rather than
    leaving a dangling fragment behind the shorter "no remote".
    """
    scan = text
    for phrase in sorted(_REMOTE_NEGATIONS, key=len, reverse=True):
        if phrase in scan:
            scan = scan.replace(phrase, " ")
    return scan


def has_remote_evidence(job, text):
    # The structured flags come from the source's own metadata and outrank
    # description prose; only the free text needs negation handling.
    if job.is_remote:
        return True
    if "remote" in job.location.lower():
        return True
    return bool(match_keywords(neutralize_remote_negations(text), _REMOTE_KEYWORDS))


def has_relocation_evidence(text):
    # Blocker phrases contain evidence keywords ("no visa sponsorship" contains
    # "visa sponsorship"), so neutralize them first — otherwise a denial would
    # self-register as a positive relocation offer.
    scan = text
    for phrase in _RELOCATION_BLOCKERS:
        if phrase in scan:
            scan = scan.replace(phrase, " ")
    pattern = "|".join(re.escape(kw) for kw in RELOCATION_KEYWORDS)
    return bool(re.search(pattern, scan))


def has_relocation_blocker(text):
    return any(phrase in text for phrase in _RELOCATION_BLOCKERS)


def remote_filter(job):
    text = job_text(job)
    remote_evidence = has_remote_evidence(job, text)
    # Judge the on-site conflict on the same negation-neutralized text, so a
    # hybrid posting whose only "remote" mention is a denial can't clear the
    # conflict check on that denial.
    scan = neutralize_remote_negations(text)
    onsite_conflict = bool(match_keywords(scan, _HYBRID_OR_ONSITE_KEYWORDS))

    if not remote_evidence:
        return False

    if onsite_conflict and "remote" not in scan:
        return False

    return True


def relocation_filter(job, relocation_regions=None):
    relocation_regions = relocation_regions or DEFAULT_RELOCATION_REGIONS
    region = job.region if job.region != Region.UNKNOWN else classify_region(job)
    if region not in relocation_regions:
        return False

    text = job_text(job)
    # A blocker phrase only disqualifies when the posting shows no sponsorship /
    # relocation offer — many listings carry boilerplate authorization wording
    # alongside an explicit "visa sponsorship available".
    if has_relocation_blocker(text) and not has_relocation_evidence(text):
        return False

    if is_eu_member_job(job):
        return True

    if job.source.lower() in RELOCATION_GUARANTEED_SOURCES:
        return True

    return has_relocation_evidence(text)


def opportunity_filter(job, relocation_regions=None):
    if is_israel_job(job):
        return True  # LLM in criteria.md judges office-days requirement
    return remote_filter(job) or relocation_filter(job, relocation_regions)


def dedup(jobs):
    """Collapse duplicate postings, MERGING richer fields into the kept record.

    Rather than dropping whichever duplicate arrives later (which could discard
    the fuller description, a better URL, or a known date), a duplicate is merged
    into the record it shares an identity key with. A posting carries up to two
    identity aliases (URL and title/company/location), so duplicates form a graph
    that this collapses with union-find semantics:

    - A single incoming job can bridge two records that were previously distinct
      (it shares one alias with each); all bridged records are unioned.
    - Merging complementary fields can SYNTHESIZE an alias no input carried (a
      title from one record + a company from another form a title|company key
      present in neither); if that alias collides with a still-distinct group,
      that group is absorbed too, repeating until the survivor's key set is
      closed. Each absorb tombstones one group, so this settles in finite steps.

    Survivors keep the earliest slot they were merged into and every key of the
    unioned group is remapped to it with a plain assignment (not setdefault, so
    no stale mapping can strand a later duplicate). Identity-less jobs are kept
    as-is; first-appearance order of distinct identities is preserved.
    """
    result = []
    group_keys = []  # keys owned by each slot; None once merged away or identity-less
    key_to_index = {}

    def absorb(idx, other):
        """Union slot `other` into `idx`, tombstoning `other` (dropped later)."""
        result[idx] = merge_jobs(result[idx], result[other])
        group_keys[idx] |= group_keys[other]
        result[other] = None
        group_keys[other] = None

    for job in jobs:
        keys = set(job_identity_keys(job))
        if not keys:
            result.append(job)
            group_keys.append(None)
            continue
        # Every distinct existing record this job's own keys touch, earliest first.
        matched = sorted({key_to_index[key] for key in keys if key in key_to_index})
        if not matched:
            result.append(job)
            group_keys.append(set(keys))
            idx = len(result) - 1
        else:
            idx = matched[0]
            result[idx] = merge_jobs(result[idx], job)
            group_keys[idx] |= keys
            for other in matched[1:]:
                absorb(idx, other)
        # Close the survivor's key set: register every alias the merged record now
        # has (including any synthesized by the merge), absorbing any live group a
        # synthesized alias collides with. Sorted drain keeps absorption order
        # deterministic; the `not None` guard skips groups tombstoned this pass.
        pending = sorted(set(job_identity_keys(result[idx])) - group_keys[idx])
        while pending:
            key = pending.pop()
            if key in group_keys[idx]:
                continue
            group_keys[idx].add(key)
            collide = key_to_index.get(key)
            if collide is not None and collide != idx and result[collide] is not None:
                # Keep the EARLIEST slot as the survivor (as the main merge path
                # does), so first-appearance order and tie-breaking stay stable
                # even when the colliding group predates this one.
                idx, gone = (idx, collide) if idx < collide else (collide, idx)
                absorb(idx, gone)
                pending = sorted(set(job_identity_keys(result[idx])) - group_keys[idx])
        for key in group_keys[idx]:
            key_to_index[key] = idx
    return [job for job in result if job is not None]


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
