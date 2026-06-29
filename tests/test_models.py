"""Characterization tests for the Job/Region model and job_to_dict."""
import datetime as dt

# --- modules under test (repoint on migration) ---
from job_search.models import Job, Region, REGION_MAP, REGION_LABELS, job_to_dict


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
