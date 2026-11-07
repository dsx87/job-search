"""Prefilter area: synthetic postings x search settings through the configured filter path.

``prefilter-cases.json``: for each settings case, the postings each rule rejects
when that rule runs on its own over every posting (Decision 9; rule names are
the engine function names in engine order), the skills each posting kept by
``configured_skills_filter`` matched, and the survivors of the full configured
path ``run_pipeline(search=...)`` (apply_region, dedup and sort included).

``location.json`` and ``prefilter-keywords.json`` are the engine's location
tables and keyword lists (Decision 11). Sets are exported sorted; regex objects
inside the lists are exported as their ``.pattern`` strings.
"""
import datetime
import types
from unittest import mock

from job_search.config import CandidateConfig, SearchConfig
from job_search.filters import keywords, rules, run_pipeline
from job_search.location import classify, countries, db
from job_search.models import Job

TODAY = datetime.date(2026, 11, 1)

NOTES = [
    "prefilter: today is 2026-11-01; filter_by_age keeps postings dated on or after "
    "today - max_age_days and postings with no date; max_age_days <= 0 disables the rule.",
    "prefilter (Decision 10): the exporter calls run_pipeline(..., relocation_regions=None), so "
    "the search setting relocation_regions is honoured. The engine's daily path "
    "(sources.fetch.fetch_jobs_with_health) passes DEFAULT_RELOCATION_REGIONS (eu, ca, us) "
    "instead and ignores the setting; Swift honours the setting.",
    "prefilter: per-rule rejections come from running each rule on its own over every posting, "
    "so one posting can appear under several rules; the engine itself logs no rule names.",
    "prefilter: location.json countries._CITY_COUNTRIES is an array of [city, country] pairs in the "
    "dict's insertion order: the literal entries in source order, then the cities the four "
    "update() calls add (IL, CA, AU, US) group by group. The engine tries cities longest first with "
    "a stable sort, so equal-length cities keep this order. Only the order inside each update "
    "group is set order (PYTHONHASHSEED); there the export uses code-point order.",
    "prefilter: _RELOCATION_BLOCKERS are blanked in set order and _REMOTE_NEGATIONS longest "
    "first with set-order ties; the postings here avoid overlapping phrases where that order "
    "would change the result.",
]

RULES = (
    "configured_location_filter",
    "configured_role_filter",
    "configured_skills_filter",
    "configured_opportunity_filter",
    "filter_by_age",
)


def _days_ago(days):
    return (TODAY - datetime.timedelta(days=days)).isoformat()


_SWIFT_DESC = "We build iOS apps in Swift and SwiftUI for millions of users."

POSTINGS = [
    {"name": "remote_ios", "title": "Senior iOS Engineer", "company": "Acme Corp", "location": "Remote",
     "url": "https://jobs.example.com/acme/1", "source": "remotive", "date_posted": _days_ago(5),
     "description": "<p>" + _SWIFT_DESC + " Fully remote team.</p>", "is_remote": True},
    {"name": "remote_ios_duplicate_richer", "title": "Senior iOS Engineer", "company": "Acme Corp", "location": "Remote",
     "url": "https://JOBS.example.com/acme/1/", "source": "weworkremotely", "date_posted": _days_ago(2),
     "description": _SWIFT_DESC + " Fully remote team. Async culture, flexible hours and a generous budget.",
     "is_remote": False},
    {"name": "berlin_onsite_eu_member", "title": "iOS Developer", "company": "Example GmbH", "location": "Berlin, Germany",
     "url": "https://jobs.example.com/example/2", "source": "remoteok", "date_posted": _days_ago(10),
     "description": _SWIFT_DESC + " Office-based in our Berlin studio.", "is_remote": False},
    {"name": "london_hybrid_visa_offered", "title": "iOS Engineer", "company": "Sample Ltd", "location": "London, UK",
     "url": "https://jobs.example.com/sample/3", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " Hybrid, three days in office. Visa sponsorship available.", "is_remote": False},
    {"name": "toronto_blocker_only", "title": "iOS Engineer", "company": "Maple Example", "location": "Toronto, Canada",
     "url": "https://jobs.example.com/maple/4", "source": "remotive", "date_posted": _days_ago(4),
     "description": _SWIFT_DESC + " On-site. We cannot sponsor work visas.", "is_remote": False},
    {"name": "austin_remote_denied", "title": "iOS Engineer", "company": "Lone Star Example", "location": "Austin, TX",
     "url": "https://jobs.example.com/lonestar/5", "source": "remotive", "date_posted": _days_ago(6),
     "description": _SWIFT_DESC + " Remote work is not available for this role.", "is_remote": False},
    {"name": "paris_not_remote_hybrid", "title": "iOS Engineer", "company": "Exemple SA", "location": "Paris, France",
     "url": "https://jobs.example.com/exemple/6", "source": "remotive", "date_posted": _days_ago(7),
     "description": _SWIFT_DESC + " This is a hybrid role, not remote.", "is_remote": False},
    {"name": "toronto_no_relocation_phrase", "title": "iOS Engineer", "company": "North Example", "location": "Toronto, Canada",
     "url": "https://jobs.example.com/north/7", "source": "remotive", "date_posted": _days_ago(8),
     "description": _SWIFT_DESC + " On-site only, no relocation.", "is_remote": False},
    {"name": "sao_paulo_onsite_local", "title": "iOS Engineer", "company": "Exemplo Ltda", "location": "São Paulo, Brazil",
     "url": "https://jobs.example.com/exemplo/8", "source": "remotive", "date_posted": _days_ago(9),
     "description": _SWIFT_DESC + " On-site in our office.", "is_remote": False},
    {"name": "lisbon_onsite_resident_unauthorized", "title": "iOS Engineer", "company": "Exemplo Lda", "location": "Lisbon, Portugal",
     "url": "https://jobs.example.com/exemplo/9", "source": "remotive", "date_posted": _days_ago(9),
     "description": _SWIFT_DESC + " On-site in our office.", "is_remote": False},
    {"name": "lagos_excluded_location", "title": "iOS Engineer", "company": "Example Nigeria", "location": "Lagos, Nigeria",
     "url": "https://jobs.example.com/lagos/10", "source": "remotive", "date_posted": _days_ago(2),
     "description": _SWIFT_DESC + " Remote friendly.", "is_remote": True},
    {"name": "android_title", "title": "Senior Android Engineer", "company": "Acme Corp", "location": "Remote",
     "url": "https://jobs.example.com/acme/11", "source": "remotive", "date_posted": _days_ago(2),
     "description": "Kotlin and Jetpack Compose. Swift is a plus for our iOS app.", "is_remote": True},
    {"name": "designer_title", "title": "Product Designer", "company": "Acme Corp", "location": "Remote",
     "url": "https://jobs.example.com/acme/12", "source": "remotive", "date_posted": _days_ago(2),
     "description": "Design iOS screens in Figma.", "is_remote": True},
    {"name": "empty_title", "title": "", "company": "Acme Corp", "location": "Remote",
     "url": "https://jobs.example.com/acme/13", "source": "remotive", "date_posted": _days_ago(2),
     "description": _SWIFT_DESC, "is_remote": True},
    {"name": "no_skills_match", "title": "iOS Engineer", "company": "Kotlin Shop", "location": "Remote",
     "url": "https://jobs.example.com/kotlin/14", "source": "remotive", "date_posted": _days_ago(2),
     "description": "Mobile apps in Objective-C. Swiftly growing team.", "is_remote": True},
    {"name": "too_old", "title": "iOS Engineer", "company": "Old Example", "location": "Remote",
     "url": "https://jobs.example.com/old/15", "source": "remotive", "date_posted": _days_ago(31),
     "description": _SWIFT_DESC, "is_remote": True},
    {"name": "exactly_at_cutoff", "title": "iOS Engineer", "company": "Edge Example", "location": "Remote",
     "url": "https://jobs.example.com/edge/16", "source": "remotive", "date_posted": _days_ago(30),
     "description": _SWIFT_DESC, "is_remote": True},
    {"name": "week_old_boundary", "title": "iOS Engineer", "company": "Week Example", "location": "Remote",
     "url": "https://jobs.example.com/week/17", "source": "remotive", "date_posted": _days_ago(8),
     "description": _SWIFT_DESC, "is_remote": True},
    {"name": "unknown_date", "title": "iOS Engineer", "company": "Undated Example", "location": "Remote",
     "url": "https://jobs.example.com/undated/18", "source": "remotive", "date_posted": None,
     "description": _SWIFT_DESC, "is_remote": True},
    {"name": "sydney_relocation_offered", "title": "iOS Engineer", "company": "Down Under Example", "location": "Sydney, Australia",
     "url": "https://jobs.example.com/aus/19", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " On-site. Relocation package provided.", "is_remote": False},
    {"name": "remote_in_location_field", "title": "iOS Engineer", "company": "Field Example", "location": "Remote - US",
     "url": "https://jobs.example.com/field/20", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " On-site kickoff once a year.", "is_remote": False},
    {"name": "zurich_relocate_me", "title": "iOS Engineer", "company": "Swiss Example AG", "location": "Zurich, Switzerland",
     "url": "https://jobs.example.com/swiss/21", "source": "relocate.me", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " On-site in Zurich.", "is_remote": False},
    {"name": "remote_flag_outranks_text", "title": "iOS Engineer", "company": "Flag Example", "location": "Toronto, Canada",
     "url": "https://jobs.example.com/flag/22", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " This role is not remote.", "is_remote": True},
    {"name": "hybrid_with_remote_days", "title": "iOS Engineer", "company": "Mixed Example", "location": "Vienna, Austria",
     "url": "https://jobs.example.com/mixed/23", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " Hybrid: remote two days a week.", "is_remote": False},
    {"name": "toronto_blocker_and_offer", "title": "iOS Engineer", "company": "Both Example", "location": "Toronto, Canada",
     "url": "https://jobs.example.com/both/24", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " Must be authorized to work in Canada; relocation assistance offered.",
     "is_remote": False},
    {"name": "unknown_region_onsite", "title": "iOS Engineer", "company": "Nowhere Example", "location": "Atlantis",
     "url": "https://jobs.example.com/nowhere/25", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " On-site. Relocation support available.", "is_remote": False},
    {"name": "toronto_onsite_ca_authorized", "title": "iOS Engineer", "company": "Leaf Example", "location": "Toronto, ON",
     "url": "https://jobs.example.com/leaf/26", "source": "remotive", "date_posted": _days_ago(3),
     "description": _SWIFT_DESC + " On-site in the office.", "is_remote": False},
]

_IOS_SEARCH = {
    "role_include_terms": ["ios engineer", "ios developer", "  Senior iOS  "],
    "role_exclude_terms": ["android", "designer", ""],
    "skill_include_groups": [["swift", "swiftui"], ["ios", "ipados"], []],
    "location_exclude_terms": ["nigeria", " "],
}

# Fake but valid country codes, not tied to any person.
_CANDIDATE_LOCAL = {"residency_countries": ["BR", "PT", "CA"], "work_authorization_countries": ["BR", "CA"]}

SETTINGS = [
    {"name": "engine_defaults", "search": {}, "candidate": None},
    {"name": "ios_search", "search": dict(_IOS_SEARCH), "candidate": None},
    {"name": "remote_only", "search": {**_IOS_SEARCH, "relocation_allowed": False}, "candidate": None},
    {"name": "relocation_only_eu", "search": {**_IOS_SEARCH, "remote_allowed": False, "relocation_regions": ["eu"]},
     "candidate": None},
    {"name": "relocation_regions_all_uppercase_and_unknown",
     "search": {**_IOS_SEARCH, "remote_allowed": False, "relocation_regions": ["EU", " ca ", "AU", "us", "xx", "mars"]},
     "candidate": None},
    {"name": "relocation_regions_empty", "search": {**_IOS_SEARCH, "remote_allowed": False, "relocation_regions": []},
     "candidate": None},
    {"name": "neither_allowed_candidate_local",
     "search": {**_IOS_SEARCH, "remote_allowed": False, "relocation_allowed": False},
     "candidate": _CANDIDATE_LOCAL},
    {"name": "candidate_local_with_defaults", "search": dict(_IOS_SEARCH), "candidate": _CANDIDATE_LOCAL},
    {"name": "candidate_residency_only",
     "search": {**_IOS_SEARCH, "remote_allowed": False, "relocation_allowed": False},
     "candidate": {"residency_countries": ["BR", "PT"], "work_authorization_countries": []}},
    {"name": "max_age_7", "search": {**_IOS_SEARCH, "max_age_days": 7}, "candidate": None},
    {"name": "max_age_0_disabled", "search": {**_IOS_SEARCH, "max_age_days": 0}, "candidate": None},
    {"name": "max_age_negative_disabled", "search": {**_IOS_SEARCH, "max_age_days": -5}, "candidate": None},
]


class _FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return TODAY


# filter_by_age reads dt.date.today() through rules' own ``dt`` binding.
_FIXED_DT = types.SimpleNamespace(date=_FixedDate, timedelta=datetime.timedelta)


def _job(posting):
    return Job.from_dict({key: value for key, value in posting.items() if key != "name"})


def _search(values):
    return SearchConfig(**values)


def _candidate(values):
    return None if values is None else CandidateConfig(**values)


def _rule_results(search, candidate):
    def rejected(keep):
        return [posting["name"] for posting in POSTINGS if not keep(_job(posting))]

    skills = {}
    for posting in POSTINGS:
        job = _job(posting)
        if rules.configured_skills_filter(job, search):
            skills[posting["name"]] = list(job.matched_skills)

    jobs = [_job(posting) for posting in POSTINGS]
    age_kept = {id(job) for job in rules.filter_by_age(jobs, search.max_age_days)}
    return {
        "configured_location_filter": {"rejected": rejected(lambda job: rules.configured_location_filter(job, search))},
        "configured_role_filter": {"rejected": rejected(lambda job: rules.configured_role_filter(job, search))},
        "configured_skills_filter": {
            "rejected": [posting["name"] for posting in POSTINGS if posting["name"] not in skills],
            "matched_skills": skills,
        },
        "configured_opportunity_filter": {
            "rejected": rejected(lambda job: rules.configured_opportunity_filter(job, search, candidate, None)),
        },
        "filter_by_age": {
            "max_age_days": search.max_age_days,
            "rejected": [posting["name"] for posting, job in zip(POSTINGS, jobs) if id(job) not in age_kept],
        },
    }


def _cases():
    cases = []
    for setting in SETTINGS:
        search = _search(setting["search"])
        candidate = _candidate(setting["candidate"])
        survivors = run_pipeline(
            [_job(posting) for posting in POSTINGS],
            max_age_days=None, relocation_regions=None, search=search, candidate=candidate,
        )
        cases.append({
            "name": setting["name"],
            "search": setting["search"],
            "candidate": setting["candidate"],
            "effective_search": {
                field: getattr(search, field)
                for field in (
                    "role_include_terms", "role_exclude_terms", "skill_include_groups",
                    "location_exclude_terms", "relocation_regions", "max_age_days",
                    "remote_allowed", "relocation_allowed",
                )
            },
            "rules": _rule_results(search, candidate),
            "survivors": [job.to_dict() for job in survivors],
        })
    return cases


def _sorted(values):
    return sorted(values)


def _location_tables():
    return {
        "db": {
            "EU_COUNTRIES": _sorted(db.EU_COUNTRIES),
            "EU_CITIES": _sorted(db.EU_CITIES),
            "_NON_EU_BROAD_REGION_TOKENS": _sorted(db._NON_EU_BROAD_REGION_TOKENS),
            "EU_MEMBER_COUNTRIES": _sorted(db.EU_MEMBER_COUNTRIES),
            "EU_MEMBER_CITIES": _sorted(db.EU_MEMBER_CITIES),
            "IL_LOCATIONS": _sorted(db.IL_LOCATIONS),
            "CA_LOCATIONS": _sorted(db.CA_LOCATIONS),
            "AU_LOCATIONS": _sorted(db.AU_LOCATIONS),
            "US_LOCATIONS": _sorted(db.US_LOCATIONS),
            "_SHORT_LOCATION_TOKENS": _sorted(db._SHORT_LOCATION_TOKENS),
            "COUNTRY_NAMES": dict(db.COUNTRY_NAMES),
        },
        "classify": {
            "_POSITIVE_EU_TOKENS": _sorted(classify._POSITIVE_EU_TOKENS),
            "_EU_MEMBER_COUNTRY_NAMES": _sorted(classify._EU_MEMBER_COUNTRY_NAMES),
            "_BROAD_EUROPE_TOKENS": _sorted(classify._BROAD_EUROPE_TOKENS),
            "_NON_EU_LOCATION_TOKENS": _sorted(classify._NON_EU_LOCATION_TOKENS),
            "_INCLUSIVE_LOCATION_TOKENS": _sorted(classify._INCLUSIVE_LOCATION_TOKENS),
        },
        "countries": {
            "ISO_COUNTRY_CODES": _sorted(countries.ISO_COUNTRY_CODES),
            "EU_COUNTRY_CODES": _sorted(countries.EU_COUNTRY_CODES),
            "_COUNTRY_ALIASES": dict(countries._COUNTRY_ALIASES),
            "_CITY_COUNTRIES": _city_countries_in_insertion_order(),
            "_CITY_SUBDIVISION_COUNTRIES": sorted(
                [{"city": city, "subdivision": subdivision, "country": country}
                 for (city, subdivision), country in countries._CITY_SUBDIVISION_COUNTRIES.items()],
                key=lambda item: (item["city"], item["subdivision"]),
            ),
            "_AMBIGUOUS_SUBDIVISION_CODES": _sorted(countries._AMBIGUOUS_SUBDIVISION_CODES),
            "_US_STATE_CODES": _sorted(countries._US_STATE_CODES),
            "_US_STATE_NAMES": _sorted(countries._US_STATE_NAMES),
            "_CA_PROVINCE_CODES": _sorted(countries._CA_PROVINCE_CODES),
            "_CA_PROVINCE_NAMES": _sorted(countries._CA_PROVINCE_NAMES),
        },
    }


def _keyword_tables():
    return {
        # The lists the configured path reads; the rest serve only the legacy (search=None) path.
        "used_by_configured_path": [
            "_INFLECTIONS", "RELOCATION_KEYWORDS", "DEFAULT_RELOCATION_REGIONS",
            "RELOCATION_GUARANTEED_SOURCES", "_REMOTE_KEYWORDS", "_REMOTE_NEGATIONS",
            "_HYBRID_OR_ONSITE_KEYWORDS", "_RELOCATION_BLOCKERS",
        ],
        "keywords": {
            "SKILL_KEYWORDS": list(keywords.SKILL_KEYWORDS),
            "_INFLECTIONS": keywords._INFLECTIONS,
            "_ALIASES": {key: list(value) for key, value in keywords._ALIASES.items()},
            "_APPLE_CONTEXT": keywords._APPLE_CONTEXT,
            "_EXTRA_PATTERNS": {
                key: [pattern.pattern for pattern in value] for key, value in keywords._EXTRA_PATTERNS.items()
            },
        },
        "rules": {
            "RELOCATION_KEYWORDS": list(rules.RELOCATION_KEYWORDS),
            "DEFAULT_RELOCATION_REGIONS": _sorted(region.value for region in rules.DEFAULT_RELOCATION_REGIONS),
            "RELOCATION_GUARANTEED_SOURCES": _sorted(rules.RELOCATION_GUARANTEED_SOURCES),
            "_PLATFORM_KEYWORDS": _sorted(rules._PLATFORM_KEYWORDS),
            "_APPLE_FRAMEWORK_KEYWORDS": _sorted(rules._APPLE_FRAMEWORK_KEYWORDS),
            "_LANGUAGE_KEYWORDS": _sorted(rules._LANGUAGE_KEYWORDS),
            "_STRONG_TITLE_LANGUAGE_KEYWORDS": _sorted(rules._STRONG_TITLE_LANGUAGE_KEYWORDS),
            "_NON_APPLE_STACK_KEYWORDS": _sorted(rules._NON_APPLE_STACK_KEYWORDS),
            "_REMOTE_KEYWORDS": _sorted(rules._REMOTE_KEYWORDS),
            "_REMOTE_NEGATIONS": _sorted(rules._REMOTE_NEGATIONS),
            "_HYBRID_OR_ONSITE_KEYWORDS": _sorted(rules._HYBRID_OR_ONSITE_KEYWORDS),
            "_RELOCATION_BLOCKERS": _sorted(rules._RELOCATION_BLOCKERS),
            "REGION_SORT_ORDER": {region.value: order for region, order in rules.REGION_SORT_ORDER.items()},
        },
    }



def _city_countries_in_insertion_order():
    """`_CITY_COUNTRIES` as [city, country] pairs in the dict's insertion order.

    The literal part is read from the engine source (its order is the source order); the cities
    the update() calls add follow, group by group, each group in code-point order because the
    engine builds it from a set. The result is checked against the live dict.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(countries))
    literal = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_CITY_COUNTRIES" for t in node.targets)
    )
    order = list(literal)
    groups = [
        countries.IL_LOCATIONS,
        countries.CA_LOCATIONS - {"canada"},
        countries.AU_LOCATIONS - {"australia"},
        countries.US_LOCATIONS - {"united states", "usa", "us", "u.s.", "u.s.a."},
    ]
    seen = set(order)
    for group in groups:
        for city in sorted(group):
            if city not in seen:
                seen.add(city)
                order.append(city)
    live = countries._CITY_COUNTRIES
    if set(order) != set(live) or len(order) != len(live):
        raise RuntimeError("_CITY_COUNTRIES export does not cover the live dict")
    if list(live)[:len(literal)] != list(literal):
        raise RuntimeError("_CITY_COUNTRIES literal part is not first in the live dict")
    return [[city, live[city]] for city in order]

def export():
    names = [posting["name"] for posting in POSTINGS]
    assert len(names) == len(set(names)), "duplicate posting name"
    with mock.patch.object(rules, "dt", _FIXED_DT):
        cases = _cases()
    return {
        "prefilter-cases.json": {
            "today": TODAY.isoformat(),
            "rules": list(RULES),
            "postings": POSTINGS,
            "cases": cases,
        },
        "location.json": _location_tables(),
        "prefilter-keywords.json": _keyword_tables(),
    }
