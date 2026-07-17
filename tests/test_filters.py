"""Characterization tests for the filter rules and run_pipeline."""
import datetime as dt

import pytest

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
    assert relocation_filter(
        Job(location="Berlin, Germany", description="Normal full-time iOS role"),
        {Region.EU},
    ) is True
    assert relocation_filter(
        Job(location="Berlin, Germany", description="Local candidates only"),
        {Region.EU},
    ) is False
    assert relocation_filter(
        Job(location="London, United Kingdom", description="Normal role"),
        {Region.EU},
    ) is False
    assert relocation_filter(
        Job(location="London, United Kingdom", description="Visa sponsorship available"),
        {Region.EU},
    ) is True
    assert relocation_filter(
        Job(location="Berlin, Germany", description="Normal role"),
        {Region.US},
    ) is False
    assert relocation_filter(
        Job(
            source="relocate.me",
            location="Berlin, Germany",
            description="Applicants must already be authorized to work",
        ),
        {Region.EU},
    ) is False


def test_relocation_filter_boilerplate_auth_phrase_does_not_block():
    # "must have the right to work" is common boilerplate in sponsoring listings;
    # it must no longer block an otherwise-qualifying EU-member role.
    assert relocation_filter(
        Job(location="Berlin, Germany", description="You must have the right to work in the EU."),
        {Region.EU},
    ) is True


def test_relocation_filter_blocker_yields_to_sponsorship_evidence():
    # A blocker phrase alongside an explicit sponsorship offer must not filter the job.
    assert relocation_filter(
        Job(location="London, United Kingdom", description="EU citizens only, but visa sponsorship available"),
        {Region.EU},
    ) is True
    # ...but the same blocker with no sponsorship offer still disqualifies.
    assert relocation_filter(
        Job(location="London, United Kingdom", description="EU citizens only. No sponsorship."),
        {Region.EU},
    ) is False


def test_relocation_filter_rejects_northern_ireland_without_evidence():
    job = Job(
        title="iOS Engineer",
        company="Acme",
        location="Belfast, Northern Ireland",
        description="Build an onsite iOS application with Swift and UIKit.",
    )

    assert relocation_filter(job, {Region.EU}) is False


@pytest.mark.parametrize(
    "location",
    [
        "Belfast, Northern-Ireland",
        "Belfast, Northern  Ireland",
        "Remote - non-EU",
        "Dublin, Ohio, USA",
        "Athens, Georgia, USA",
        "Dublin, Canada",
        "Athens, Australia",
        "Dublin, United Kingdom",
        "Belfast, N. Ireland",
        "Dublin, GA",
        "Athens, OH",
    ],
)
def test_relocation_filter_rejects_non_eu_and_conflicting_locations(location):
    job = Job(
        title="iOS Engineer",
        company="Acme",
        location=location,
        description="Build an onsite iOS application with Swift and UIKit.",
    )

    assert relocation_filter(job, {Region.EU}) is False


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


def test_dedup_keeps_unrelated_url_less_jobs_distinct():
    jobs = [
        Job(title="iOS Engineer", company="Acme"),
        Job(title="macOS Engineer", company="Beta"),
        Job(title=" iOS Engineer ", company="ACME"),
        Job(),
        Job(),
    ]

    assert dedup(jobs) == [jobs[0], jobs[1], jobs[3], jobs[4]]


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
            url="https://x/on", description="strictly onsite, local candidates only", is_remote=False, date_posted=today),
        Job(title="macOS Engineer", company="Cupertino", location="Remote",
            url="https://x/mac", description="Swift, remote position", is_remote=True, date_posted=today),
    ]
    out = run_pipeline(raw, max_age_days=30)
    assert [(j.title, j.region.value) for j in out] == [
        ("iOS Engineer", "EU"),
        ("macOS Engineer", "UNKNOWN"),
    ]


def test_run_pipeline_keeps_onsite_eu_member_role_without_blocker():
    job = Job(
        title="iOS Engineer",
        company="Acme",
        location="Berlin, Germany",
        url="https://x/berlin",
        description="Build an onsite iOS application with Swift and UIKit.",
    )

    assert run_pipeline([job], max_age_days=30) == [job]


# --- audit order 5: dedup now MERGES duplicates instead of dropping them ---
def test_dedup_merges_richer_description_from_later_duplicate():
    # Same url; the SECOND posting carries the richer description -> richness, not first-wins.
    jobs = [
        Job(url="https://x/1", title="iOS", company="Acme", description="short"),
        Job(
            url="https://x/1",
            title="iOS",
            company="Acme",
            description="a much longer and richer description with detail",
        ),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].description == "a much longer and richer description with detail"


def test_dedup_merges_url_and_description_into_url_less_first_record():
    # First record has no url but the same title|company key as the second.
    jobs = [
        Job(title="iOS", company="Acme"),
        Job(title="iOS", company="Acme", url="https://x/1", description="fuller"),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].url == "https://x/1"
    assert out[0].description == "fuller"


def test_dedup_merges_remote_date_and_region_across_duplicates():
    jobs = [
        Job(
            url="https://x/1",
            title="iOS",
            company="Acme",
            is_remote=False,
            date_posted=dt.date(2024, 1, 1),
            region=Region.UNKNOWN,
        ),
        Job(
            url="https://x/1",
            title="iOS",
            company="Acme",
            is_remote=True,
            date_posted=dt.date(2024, 3, 1),
            region=Region.EU,
        ),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].is_remote is True
    assert out[0].date_posted == dt.date(2024, 3, 1)
    assert out[0].region == Region.EU


def test_dedup_chains_third_duplicate_into_same_record():
    # After the first two merge, the merged record's keys (incl. the url) must be
    # registered so a third duplicate sharing the url merges into the same record.
    jobs = [
        Job(title="iOS", company="Acme"),
        Job(title="iOS", company="Acme", url="https://x/1"),
        Job(url="https://x/1", title="iOS Dev", company="Acme", description="third"),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].url == "https://x/1"
    assert out[0].description == "third"


def test_dedup_unions_two_prior_records_bridged_by_a_later_job():
    # A posting carries up to two identity aliases (url and title|company|location).
    # Records A and B share NO key, so they start distinct. C shares A's url and
    # B's title|company key, bridging them -- all three must collapse into ONE
    # record. (The pre-union code merged C into A but left B stranded.)
    jobs = [
        Job(url="https://x/1", title="iOS Engineer", company="Acme"),      # A
        Job(url="https://x/2", title="Senior iOS", company="Acme"),        # B
        Job(url="https://x/1", title="Senior iOS", company="Acme",         # C bridges A+B
            description="fullest description across all three"),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].description == "fullest description across all three"
    # The bridged record's keys are all remapped, so a 4th duplicate arriving via
    # B's url still folds into the same record instead of spawning a new one.
    out2 = dedup(jobs + [Job(url="https://x/2", title="ignored", company="ignored")])
    assert len(out2) == 1


def test_dedup_registers_identity_alias_synthesized_by_a_merge():
    # A carries a title but no company; B (same url) carries the company but no
    # title. Merging them SYNTHESIZES a title|company alias that neither input
    # had. A later duplicate C matching only that synthesized alias must still
    # fold into the merged record instead of surviving as a separate job.
    jobs = [
        Job(url="https://x/1", title="iOS Engineer"),                 # A: {url, "ios engineer|"}
        Job(url="https://x/1", company="Acme"),                       # B: {url, "|acme"}
        Job(url="https://x/2", title="iOS Engineer", company="Acme",  # C: {url2, "ios engineer|acme"}
            description="third"),
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].title == "iOS Engineer"
    assert out[0].company == "Acme"
    assert out[0].description == "third"


def test_dedup_absorbs_group_a_synthesized_alias_collides_with():
    # G is a distinct record whose title|company key is "ios engineer|acme".
    # A (title only) and B (company only) share a url, so they merge into a record
    # that SYNTHESIZES that very same title|company key. The synthesized alias
    # therefore collides with G's existing key: all three must collapse into one
    # record, not leave G stranded alongside the merged A+B.
    jobs = [
        Job(url="https://x/9", title="iOS Engineer", company="Acme", description="G"),  # G
        Job(url="https://x/1", title="iOS Engineer"),                                   # A
        Job(url="https://x/1", company="Acme", description="a much fuller posting"),    # B
    ]
    out = dedup(jobs)
    assert len(out) == 1
    assert out[0].title == "iOS Engineer"
    assert out[0].company == "Acme"
    assert out[0].description == "a much fuller posting"
    # Order-independence: G arriving last must give the same single-record result.
    reordered = dedup([jobs[1], jobs[2], jobs[0]])
    assert len(reordered) == 1


def test_dedup_keeps_all_empty_jobs():
    # An all-empty Job() has no identity keys and is never merged.
    jobs = [Job(), Job(), Job()]
    out = dedup(jobs)
    assert len(out) == 3
    assert out == jobs
