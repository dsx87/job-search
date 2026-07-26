"""Characterization tests for the Job/Region model and serialization."""
import datetime as dt
from dataclasses import is_dataclass

# --- modules under test (repoint on migration) ---
from job_search.models import (
    REGION_LABELS,
    REGION_MAP,
    Job,
    Region,
    _best_date,
    coerce_job,
    job_to_dict,
    merge_jobs,
)


def test_job_defaults():
    j = Job()
    assert j.title == ""
    assert j.matched_skills == []
    assert j.region == Region.UNKNOWN
    assert j.is_remote is False
    assert j.date_posted is None


def test_job_strips_fields_but_preserves_description():
    j = Job(title="  iOS Dev  ", company=" Acme ", location=" Berlin ", description="  raw desc  ", is_remote=1)
    assert j.title == "iOS Dev"
    assert j.company == "Acme"
    assert j.location == "Berlin"
    assert j.description == "  raw desc  "  # description is NOT stripped
    assert j.is_remote is True


def test_job_matched_skills_is_independent_list():
    base = ["ios"]
    j = Job(matched_skills=base)
    j.matched_skills.append("swift")
    assert base == ["ios"]  # constructor copies the list


def test_job_is_mutable_dataclass():
    job = Job(title="Before")

    assert is_dataclass(job)
    job.title = "After"
    assert job.title == "After"


def test_job_complete_dict_round_trip_normalizes_date_region_and_ignores_extras():
    data = {
        "title": "  iOS Engineer  ",
        "company": " Acme ",
        "location": " Berlin ",
        "url": " https://x/1 ",
        "source": " remotive ",
        "date_posted": "2024-01-02",
        "description": "  complete description  ",
        "is_remote": 1,
        "region": "eu",
        "matched_skills": ["ios", "swift"],
        "seen": True,
        "future_field": "ignored",
    }

    job = Job.from_dict(data)

    assert job.to_dict() == {
        "title": "iOS Engineer",
        "company": "Acme",
        "location": "Berlin",
        "url": "https://x/1",
        "source": "remotive",
        "date_posted": "2024-01-02",
        "description": "  complete description  ",
        "is_remote": True,
        "region": "EU",
        "matched_skills": ["ios", "swift"],
    }
    assert Job.from_dict(job.to_dict()) == job


def test_job_from_dict_accepts_datetime_and_unknown_region():
    posted = dt.datetime(2024, 1, 2, 3, 4, 5)

    # finding 11: a datetime is narrowed to a date at construction, so every Job
    # satisfies the invariant filter_by_age and the first-run cutoff assume. It
    # used to be preserved as a datetime, which made rules.filter_by_age compare
    # datetime >= date and raise TypeError.
    assert Job.from_dict({"date_posted": posted}).date_posted == dt.date(2024, 1, 2)
    assert Job.from_dict({"region": "somewhere"}).region is Region.UNKNOWN


def test_job_date_posted_is_always_a_plain_date():
    assert Job(date_posted=dt.datetime(2024, 6, 1, 12, 0)).date_posted == dt.date(2024, 6, 1)
    assert Job(date_posted=dt.date(2024, 6, 1)).date_posted == dt.date(2024, 6, 1)
    assert Job(date_posted=None).date_posted is None


def test_filter_by_age_accepts_a_datetime_valued_source_date():
    from job_search.filters.rules import filter_by_age

    # The reproduction from finding 11, now harmless because Job normalizes.
    fresh = Job(date_posted=dt.datetime.now())
    stale = Job(date_posted=dt.datetime.now() - dt.timedelta(days=30))
    assert filter_by_age([fresh, stale], 7) == [fresh]


def test_job_serialized_skill_lists_are_independent():
    job = Job(matched_skills=["ios"])
    data = job.to_dict()
    data["matched_skills"].append("swift")

    assert job.matched_skills == ["ios"]


def test_coerce_job_preserves_jobs_and_accepts_legacy_mappings():
    job = Job(title="Canonical")

    assert coerce_job(job) is job
    assert coerce_job({"title": " Legacy ", "region": "CA"}) == Job(
        title="Legacy", region=Region.CA
    )


def test_region_maps():
    assert REGION_MAP["eu"] == Region.EU
    assert REGION_MAP["us"] == Region.US
    assert REGION_LABELS[Region.EU] == "Europe"
    assert REGION_LABELS[Region.UNKNOWN] == "Other / Unknown"


def test_job_to_dict():
    j = Job(
        title="iOS Engineer",
        company="Acme",
        location="Berlin",
        url="https://x/1",
        source="remotive",
        date_posted=dt.date(2024, 1, 2),
        is_remote=True,
        region=Region.EU,
        matched_skills=["ios", "swift"],
    )
    d = job_to_dict(j)
    assert d == {
        "title": "iOS Engineer",
        "company": "Acme",
        "location": "Berlin",
        "url": "https://x/1",
        "source": "remotive",
        "date_posted": "2024-01-02",
        "is_remote": True,
        "region": "EU",
        "matched_skills": ["ios", "swift"],
    }


def test_job_to_dict_handles_none_date():
    assert job_to_dict(Job())["date_posted"] is None


def test_job_to_dict_keeps_legacy_description_free_shape():
    assert "description" not in job_to_dict(Job(description="private CLI detail"))


# --- audit order 5: merge_jobs (combine duplicate postings, keep richest fields) ---
def test_merge_jobs_keeps_longer_description_regardless_of_order():
    short = Job(title="iOS", company="Acme", description="short")
    long = Job(
        title="iOS",
        company="Acme",
        description="a much longer and richer description with more detail",
    )
    assert merge_jobs(short, long).description == long.description
    # richness wins irrespective of argument order
    assert merge_jobs(long, short).description == long.description


def test_merge_jobs_keeps_more_specific_location():
    assert merge_jobs(Job(location="Berlin"), Job(location="Berlin, Germany")).location == "Berlin, Germany"
    # empty + non-empty -> non-empty (either order)
    assert merge_jobs(Job(location=""), Job(location="Remote")).location == "Remote"
    assert merge_jobs(Job(location="Remote"), Job(location="")).location == "Remote"


def test_merge_jobs_url_prefers_base_then_falls_back():
    # base empty -> other's url
    assert merge_jobs(Job(url=""), Job(url="https://x/1")).url == "https://x/1"
    # both present -> base's url
    assert merge_jobs(Job(url="https://a"), Job(url="https://b")).url == "https://a"


def test_merge_jobs_title_and_company_fallback_when_base_empty():
    merged = merge_jobs(Job(title="", company=""), Job(title="iOS", company="Acme"))
    assert merged.title == "iOS"
    assert merged.company == "Acme"
    # base non-empty wins over other
    merged2 = merge_jobs(Job(title="Base", company="BaseCo"), Job(title="Other", company="OtherCo"))
    assert merged2.title == "Base"
    assert merged2.company == "BaseCo"


def test_merge_jobs_date_posted_prefers_present_then_more_recent():
    d1 = dt.date(2024, 1, 2)
    # None + date -> that date
    assert merge_jobs(Job(date_posted=None), Job(date_posted=d1)).date_posted == d1
    # date + None -> that date
    assert merge_jobs(Job(date_posted=d1), Job(date_posted=None)).date_posted == d1
    # earlier + later -> later (either order)
    earlier = dt.date(2024, 1, 1)
    later = dt.date(2024, 2, 1)
    assert merge_jobs(Job(date_posted=earlier), Job(date_posted=later)).date_posted == later
    assert merge_jobs(Job(date_posted=later), Job(date_posted=earlier)).date_posted == later


def test_merge_jobs_date_posted_mixes_date_and_datetime_without_error():
    a_date = dt.date(2024, 1, 1)
    a_datetime = dt.datetime(2024, 6, 1, 12, 0, 0)
    # A datetime-valued source date is narrowed on construction (finding 11), so
    # the later day still wins and no TypeError is possible in either order.
    later = dt.date(2024, 6, 1)
    assert merge_jobs(Job(date_posted=a_date), Job(date_posted=a_datetime)).date_posted == later
    assert merge_jobs(Job(date_posted=a_datetime), Job(date_posted=a_date)).date_posted == later
    # _best_date itself stays defensive for raw values that never passed through Job.
    assert _best_date(a_date, a_datetime) is a_datetime
    assert _best_date(a_datetime, a_date) is a_datetime


def test_merge_jobs_is_remote_is_or_of_inputs():
    assert merge_jobs(Job(is_remote=False), Job(is_remote=True)).is_remote is True
    assert merge_jobs(Job(is_remote=True), Job(is_remote=False)).is_remote is True
    assert merge_jobs(Job(is_remote=False), Job(is_remote=False)).is_remote is False


def test_merge_jobs_region_prefers_base_unless_unknown():
    # base UNKNOWN -> take other's region
    assert merge_jobs(Job(region=Region.UNKNOWN), Job(region=Region.EU)).region == Region.EU
    # base known -> base wins even when other differs
    assert merge_jobs(Job(region=Region.EU), Job(region=Region.US)).region == Region.EU


def test_merge_jobs_matched_skills_union_preserves_first_seen_order():
    base = Job(matched_skills=["ios", "swift"])
    other = Job(matched_skills=["swift", "uikit"])
    assert merge_jobs(base, other).matched_skills == ["ios", "swift", "uikit"]


def test_merge_jobs_accepts_mapping_inputs_and_returns_new_job():
    merged = merge_jobs(
        {"title": "iOS", "company": "Acme", "description": "short"},
        {"title": "iOS", "company": "Acme", "description": "a considerably longer description body"},
    )
    assert isinstance(merged, Job)
    assert merged.title == "iOS"
    assert merged.description == "a considerably longer description body"
