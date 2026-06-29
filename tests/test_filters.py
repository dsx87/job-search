"""Characterization tests for the filter rules and run_pipeline."""
import datetime as dt

# --- modules under test (repoint on migration) ---
from job_search.models import Job, Region
from job_search.filters.rules import (
    india_exclusion_filter,
    role_filter,
    skills_filter,
    remote_filter,
    relocation_filter,
    opportunity_filter,
    dedup,
    filter_by_age,
    sort_jobs,
)
from job_search.filters import run_pipeline


def test_india_exclusion_filter():
    assert india_exclusion_filter(Job(title="iOS Dev", location="Bangalore, India")) is False
    assert india_exclusion_filter(Job(title="iOS Dev", location="Berlin")) is True


def test_role_filter():
    assert role_filter(Job(title="iOS Developer")) is True
    assert role_filter(Job(title="Senior iOS Architect")) is True
    assert role_filter(Job(title="")) is False
    assert role_filter(Job(title="QA Engineer")) is False
    assert role_filter(Job(title="Product Manager")) is False
    assert role_filter(Job(title="Engineering Manager")) is False


def test_skills_filter_title_apple_target_sets_matched_skills():
    j = Job(title="iOS Engineer", description="Swift, UIKit experience required")
    assert skills_filter(j) is True
    assert j.matched_skills == ["ios", "swift", "uikit"]


def test_skills_filter_rejects_non_apple_title():
    j = Job(title="Android Developer", description="Swift and Kotlin for iOS and Android")
    assert skills_filter(j) is False


def test_skills_filter_description_needs_two_signals():
    strong = Job(title="Software Engineer", description="Build with SwiftUI and Core Data in our app")
    assert skills_filter(strong) is True
    assert strong.matched_skills == ["core data", "swiftui"]

    weak = Job(title="Backend Engineer", description="Some iOS adjacent work maybe")
    assert skills_filter(weak) is False


def test_remote_filter():
    assert remote_filter(Job(title="iOS Dev", is_remote=True)) is True
    assert remote_filter(Job(title="iOS Dev", location="Remote")) is True
    assert remote_filter(Job(title="iOS Dev", description="Strictly onsite in office", location="Berlin")) is False
    # hybrid + explicit remote keyword => kept
    assert remote_filter(Job(title="iOS Dev", description="Hybrid with some remote days", location="Berlin")) is True


def test_relocation_filter():
    eu = {Region.EU, Region.CA, Region.US}
    assert relocation_filter(Job(location="Berlin, Germany", description="visa sponsorship available"), eu) is True
    assert relocation_filter(Job(location="Berlin, Germany", description="we cannot sponsor visas"), eu) is False
    assert relocation_filter(Job(location="Berlin, Germany", description="just a normal role"), eu) is False
    assert relocation_filter(Job(source="relocate.me", location="Germany", description="anything"), eu) is True
    # AU not in the default relocation set
    assert relocation_filter(Job(location="Sydney, Australia", description="visa sponsorship"), eu) is False


def test_opportunity_filter_israel_always_passes():
    assert opportunity_filter(Job(location="Tel Aviv", description="onsite only")) is True


def test_dedup_url_and_title_company_location():
    jobs = [
        Job(url="https://x.com/a/", title="iOS", company="Acme"),
        Job(url="https://x.com/a", title="iOS", company="Acme"),  # same url key
        Job(url="https://x.com/1", title="iOS Dev", company="Acme", location="Remote"),
        Job(url="https://x.com/2", title="iOS Dev", company="Acme", location="Remote"),  # same title|company|location
    ]
    out = dedup(jobs)
    assert [j.url for j in out] == ["https://x.com/a/", "https://x.com/1"]


def test_filter_by_age():
    today = dt.date.today()
    jobs = [
        Job(title="fresh", date_posted=today),
        Job(title="old", date_posted=today - dt.timedelta(days=10)),
        Job(title="undated", date_posted=None),
    ]
    kept = [j.title for j in filter_by_age(jobs, max_age_days=5)]
    assert kept == ["fresh", "undated"]
    assert len(filter_by_age(jobs, max_age_days=0)) == 3  # 0 keeps all


def test_sort_jobs_by_region_order():
    jobs = [
        Job(title="us", region=Region.US),
        Job(title="eu", region=Region.EU),
        Job(title="unknown", region=Region.UNKNOWN),
        Job(title="ca", region=Region.CA),
    ]
    assert [j.title for j in sort_jobs(jobs)] == ["eu", "ca", "us", "unknown"]


def test_run_pipeline_golden():
    today = dt.date.today()
    raw = [
        Job(title="iOS Engineer", company="Acme", location="Berlin, Germany",
            url="https://x/eu", description="Swift UIKit, fully remote role", is_remote=True, date_posted=today),
        Job(title="QA Engineer", company="Acme", location="Berlin",
            url="https://x/qa", description="ios swift", is_remote=True, date_posted=today),
        Job(title="iOS Developer", company="Globex", location="Bangalore, India",
            url="https://x/in", description="Swift UIKit remote", is_remote=True, date_posted=today),
        Job(title="Android Developer", company="Initech", location="Remote",
            url="https://x/an", description="Swift Kotlin iOS Android remote", is_remote=True, date_posted=today),
        Job(title="iOS Dev", company="Onsite Co", location="Berlin",
            url="https://x/on", description="strictly onsite in office, no visa", is_remote=False, date_posted=today),
        Job(title="macOS Engineer", company="Cupertino", location="Remote",
            url="https://x/mac", description="Swift, remote position", is_remote=True, date_posted=today),
    ]
    out = run_pipeline(raw, max_age_days=30)
    assert [(j.title, j.region.value) for j in out] == [
        ("iOS Engineer", "EU"),
        ("macOS Engineer", "UNKNOWN"),
    ]
