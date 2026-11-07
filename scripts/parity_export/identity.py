"""Identity area: alias strings, identity-key tokens, merge_jobs and dedup.

``aliases.json``: each case gives the raw fields and the engine's
``normalize_url``, ``title_company_key`` and ``identity_keys_from_fields`` on
them, then the same through a ``Job`` (whose constructor strips every field
first): ``job_identity_keys``, ``canonical_job_key`` and the ``_identity_token``
of each key (``delivery_identity_tokens``).

``merge-jobs.json``: ``merge_jobs(base, other)`` pairs. ``dedup.json``: job lists
in a fixed input order (Decision 8) and the engine's ``dedup`` output. Jobs are
given in ``Job.to_dict`` form and built with ``Job.from_dict``.
"""
from job_search.filters.rules import dedup
from job_search.identity import (
    canonical_job_key,
    identity_keys_from_fields,
    job_identity_keys,
    normalize_url,
    title_company_key,
)
from job_search.models import Job, merge_jobs
from job_search.state.seen_jobs import _identity_token, delivery_identity_tokens

LOOSE_ALIAS = "loose alias (Decision 20)"

NOTES = [
    "identity (Decision 20): cases marked intended_difference = 'loose alias (Decision 20)' "
    "carry the engine's output, which keeps the postings apart; the Swift loose alias "
    "(punctuation removed, whitespace collapsed, lowercased, trailing legal-form words dropped, "
    "location ignored) merges them.",
]

_ALIAS_CASES = [
    ("url_and_key", "https://Jobs.Example.com/Acme/1", "iOS Engineer", "Acme Corp", "Berlin, Germany"),
    ("url_trailing_slashes_and_spaces", "  https://jobs.example.com/acme/1///  ", "iOS Engineer", "Acme Corp", ""),
    ("url_only", "https://jobs.example.com/acme/2", "", "", ""),
    ("no_url", "", "iOS Engineer", "Acme Corp", "Remote"),
    ("all_empty", "", "", "", ""),
    ("whitespace_only", "   ", "  ", "\t", "\n"),
    ("location_only", "", "", "", "Berlin"),
    ("company_only", "", "", "Acme Corp", ""),
    ("title_internal_spaces_kept", "", "iOS   Engineer", "Acme  Corp", "Berlin   ,  Germany"),
    ("nbsp_inside_and_around", "", " iOS Engineer ", "Acme Corp", "Berlin, Germany "),
    ("line_separator", "", "iOS Engineer ", "Acme Corp", "Berlin Germany"),
    ("information_separators", "", "\x1ciOS Engineer\x1f", "Acme\x1dCorp", "Berlin\x1eGermany"),
    ("turkish_dotted_i", "https://jobs.example.com/İstanbul", "İOS MÜHENDİSİ", "İstanbul Example A.Ş.", "İstanbul"),
    ("sharp_s", "", "Softwareentwickler Straße", "STRASSE Example GmbH", "Großstadt"),
    ("emoji", "", "iOS Engineer \U0001f680", "Acme \U0001f469‍\U0001f4bb", "Remote \U0001f30d"),
    ("combining_marks", "", "Café Engineer", "Café Corp", "Zürich"),
    ("final_sigma", "", "ΟΔΟΣ", "Σ Example", ""),
    ("pipe_in_fields", "", "iOS | Swift", "Acme|Corp", "Berlin | Remote"),
]


def _aliases():
    cases = []
    for name, url, title, company, location in _ALIAS_CASES:
        job = Job(title=title, company=company, location=location, url=url)
        keys = job_identity_keys(job)
        cases.append({
            "name": name,
            "input": {"url": url, "title": title, "company": company, "location": location},
            "normalize_url": normalize_url(url),
            "title_company_key": title_company_key(title, company, location),
            "identity_keys_from_fields": list(identity_keys_from_fields(url, title, company, location)),
            "job_fields": {"url": job.url, "title": job.title, "company": job.company, "location": job.location},
            "job_identity_keys": list(keys),
            "canonical_job_key": canonical_job_key(job),
            "identity_tokens": [{"key": key, "token": _identity_token(key)} for key in keys],
            "delivery_identity_tokens": list(delivery_identity_tokens(url, title, company, location)),
        })
    return cases


def _job(title="iOS Engineer", company="Acme Corp", location="Berlin", url="", **fields):
    return {"title": title, "company": company, "location": location, "url": url, **fields}


_MERGE_CASES = [
    ("richer_description_other", _job(url="u1", description="short"),
     _job(url="u1", description="a much longer description")),
    ("richer_description_tie_base_wins", _job(url="u1", description="abcd"), _job(url="u1", description="wxyz")),
    ("richer_by_collapsed_length", _job(url="u1", description="a      b"), _job(url="u1", description="abcd")),
    ("richer_nbsp_counts_as_whitespace", _job(url="u1", description="a    b"),
     _job(url="u1", description="abc")),
    ("richer_location", _job(url="u1", location="Berlin"), _job(url="u1", location="Berlin,   Germany")),
    ("date_base_none", _job(url="u1", date_posted=None), _job(url="u1", date_posted="2026-10-20")),
    ("date_other_none", _job(url="u1", date_posted="2026-10-20"), _job(url="u1", date_posted=None)),
    ("date_more_recent_other", _job(url="u1", date_posted="2026-10-01"), _job(url="u1", date_posted="2026-10-20")),
    ("date_equal_base_wins", _job(url="u1", date_posted="2026-10-20"),
     _job(url="u1", date_posted="2026-10-20T23:00:00Z")),
    ("date_full_iso_stamp_narrowed", _job(url="u1", date_posted="2026-10-30T22:30:00+05:00"),
     _job(url="u1", date_posted="2026-10-20")),
    ("date_unparseable_is_unknown", _job(url="u1", date_posted="last week"), _job(url="u1", date_posted="2026-10-20")),
    ("is_remote_or", _job(url="u1", is_remote=False), _job(url="u1", is_remote=True)),
    ("region_base_unknown_takes_other", _job(url="u1", region="UNKNOWN"), _job(url="u1", region="EU")),
    ("region_base_known_wins", _job(url="u1", region="CA"), _job(url="u1", region="EU")),
    ("skills_union_in_order", _job(url="u1", matched_skills=["swift", "ios"]),
     _job(url="u1", matched_skills=["swiftui", "ios", "combine"])),
    ("empty_fields_filled_from_other", _job(title="", company="", url="", source=""),
     _job(title="iOS Engineer", company="Acme Corp", url="u2", source="remotive")),
    ("base_fields_win", _job(url="u1", source="remotive"), _job(title="Other Title", company="Other Co", url="u2",
                                                             source="remoteok")),
]


def _merge_jobs():
    return [
        {
            "name": name,
            "base": base,
            "other": other,
            "expected": merge_jobs(Job.from_dict(base), Job.from_dict(other)).to_dict(),
        }
        for name, base, other in _MERGE_CASES
    ]


_DEDUP_CASES = [
    ("same_url_case_and_slash", [
        _job(url="https://jobs.example.com/a/1", description="short", source="remotive", date_posted="2026-10-01"),
        _job(url="HTTPS://JOBS.EXAMPLE.COM/a/1/", location="Remote", description="longer description here",
             source="remoteok", is_remote=True, date_posted="2026-10-15"),
    ], None),
    ("same_key_different_urls", [
        _job(url="https://jobs.example.com/a/1", source="remotive"),
        _job(url="https://jobs.example.com/b/9", source="remoteok", description="from second source"),
    ], None),
    ("title_company_case_and_outer_space", [
        _job(title="iOS Engineer ", company="ACME CORP", url="https://jobs.example.com/a/1"),
        _job(title="ios engineer", company=" Acme Corp", url="https://jobs.example.com/a/2"),
    ], None),
    ("location_whitespace_collapsed", [
        _job(location="Berlin,  Germany", url="https://jobs.example.com/a/1"),
        _job(location="berlin, germany", url="https://jobs.example.com/a/2"),
    ], None),
    ("bridge_unions_two_groups", [
        _job(title="iOS Engineer", location="Berlin", url="https://jobs.example.com/a/1", description="first"),
        _job(title="Swift Developer", location="Remote", url="https://jobs.example.com/b/2", description="second"),
        _job(title="Swift Developer", location="Remote", url="https://jobs.example.com/a/1",
             description="third, the bridge"),
    ], None),
    ("synthesized_alias_absorbs_later_group", [
        _job(title="iOS Engineer", company="", location="", url="https://jobs.example.com/a/1", description="A"),
        _job(title="iOS Engineer", company="Acme Corp", location="", url="https://jobs.example.com/b/2",
             description="B longer"),
        _job(title="", company="Acme Corp", location="", url="https://jobs.example.com/a/1", description="C"),
    ], None),
    ("synthesized_alias_absorbs_earlier_group", [
        _job(title="iOS Engineer", company="Acme Corp", location="", url="https://jobs.example.com/x/0",
             description="X"),
        _job(title="iOS Engineer", company="", location="", url="https://jobs.example.com/a/1", description="A"),
        _job(title="", company="Acme Corp", location="", url="https://jobs.example.com/a/1",
             description="C is longest"),
    ], None),
    ("identity_less_jobs_kept", [
        _job(title="", company="", location="", url="", description="no identity one"),
        _job(title="", company="", location="", url="", description="no identity two"),
        _job(url="https://jobs.example.com/a/1"),
    ], None),
    ("order_of_first_appearance_kept", [
        _job(title="B Role", url="https://jobs.example.com/b"),
        _job(title="A Role", url="https://jobs.example.com/a"),
        _job(title="B Role", url="https://jobs.example.com/b/", description="dup of first"),
        _job(title="C Role", url="https://jobs.example.com/c"),
    ], None),
    ("three_way_merge_dates_and_remote", [
        _job(url="https://jobs.example.com/a/1", date_posted="2026-10-10"),
        _job(url="https://jobs.example.com/a/1", date_posted=None, is_remote=True),
        _job(url="https://jobs.example.com/a/1", date_posted="2026-10-12T08:00:00Z", matched_skills=["swift"]),
    ], None),
    ("loose_alias_location_differs", [
        _job(location="Berlin", url="https://jobs.example.com/a/1"),
        _job(location="Remote", url="https://jobs.example.com/b/2"),
    ], LOOSE_ALIAS),
    ("loose_alias_legal_form_suffix", [
        _job(company="Acme GmbH", url="https://jobs.example.com/a/1"),
        _job(company="Acme", url="https://jobs.example.com/b/2"),
    ], LOOSE_ALIAS),
    ("loose_alias_punctuation_and_legal_form", [
        _job(company="Acme, Inc.", url="https://jobs.example.com/a/1"),
        _job(company="Acme Inc", url="https://jobs.example.com/b/2"),
    ], LOOSE_ALIAS),
    ("loose_alias_title_nbsp", [
        _job(title="iOS Engineer", url="https://jobs.example.com/a/1"),
        _job(title="iOS Engineer", url="https://jobs.example.com/b/2"),
    ], LOOSE_ALIAS),
    ("loose_alias_title_punctuation", [
        _job(title="Sr. iOS Engineer (Swift)", url="https://jobs.example.com/a/1"),
        _job(title="Sr iOS Engineer Swift", url="https://jobs.example.com/b/2"),
    ], LOOSE_ALIAS),
]


def _dedup():
    cases = []
    for name, jobs, difference in _DEDUP_CASES:
        case = {
            "name": name,
            "input": jobs,
            "expected": [job.to_dict() for job in dedup([Job.from_dict(job) for job in jobs])],
        }
        if difference:
            case["intended_difference"] = difference
        cases.append(case)
    return cases


def export():
    for cases in (_ALIAS_CASES, _MERGE_CASES, _DEDUP_CASES):
        names = [case[0] for case in cases]
        assert len(names) == len(set(names)), "duplicate case name"
    return {
        "aliases.json": _aliases(),
        "merge-jobs.json": _merge_jobs(),
        "dedup.json": _dedup(),
    }
