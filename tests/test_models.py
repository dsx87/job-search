"""Characterization tests for the Job/Region model and serialization."""
import datetime as dt
from dataclasses import is_dataclass

# --- modules under test (repoint on migration) ---
from job_search.models import Job, Region, REGION_MAP, REGION_LABELS, coerce_job, job_to_dict


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

    assert Job.from_dict({"date_posted": posted}).date_posted is posted
    assert Job.from_dict({"region": "somewhere"}).region is Region.UNKNOWN


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
