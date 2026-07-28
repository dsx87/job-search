"""TDD for user-defined digest sections: the predicate vocabulary."""
import datetime

from job_search.digest.model import FitEntry, ReviewEntry
from job_search.digest.sections import (
    Section,
    all_of,
    any_of,
    days_since_posted,
    fact,
    in_region,
    is_remote,
    location_contains,
    not_,
    on_job,
    title_matches,
)
from job_search.models import Job, Region


def _fit(job=None, facts=None):
    """A FitEntry carrying `job` and `facts` (evaluation={"facts": facts})."""
    return FitEntry(
        job=job if job is not None else Job(),
        evaluation={"facts": facts or {}},
        summary="",
        pdf_bytes=b"",
        cv_filename="",
    )


def test_section_defaults_to_fits_only_and_matches_everything():
    section = Section("Everything else")
    assert section.icon == ""
    assert section.applies_to == ("fits",)
    assert section.match is None


def test_is_remote_reads_the_job_flag():
    assert is_remote(_fit(Job(is_remote=True))) is True
    assert is_remote(_fit(Job(is_remote=False))) is False


def test_in_region_matches_any_listed_region():
    predicate = in_region(Region.EU, Region.CA)
    assert predicate(_fit(Job(region=Region.EU))) is True
    assert predicate(_fit(Job(region=Region.CA))) is True
    assert predicate(_fit(Job(region=Region.US))) is False


def test_fact_matches_any_listed_value_case_insensitively():
    predicate = fact("work_arrangement", "Remote", "hybrid")
    assert predicate(_fit(facts={"work_arrangement": "remote"})) is True
    assert predicate(_fit(facts={"work_arrangement": "HYBRID"})) is True
    assert predicate(_fit(facts={"work_arrangement": "onsite"})) is False
    assert predicate(_fit(facts={})) is False


def test_fact_with_no_values_means_the_fact_is_known():
    predicate = fact("seniority")
    assert predicate(_fit(facts={"seniority": "senior"})) is True
    assert predicate(_fit(facts={"seniority": "unknown"})) is False
    assert predicate(_fit(facts={})) is False


def test_location_contains_is_case_insensitive():
    predicate = location_contains("Tel Aviv", "haifa")
    assert predicate(_fit(Job(location="Remote — TEL AVIV"))) is True
    assert predicate(_fit(Job(location="Haifa, Israel"))) is True
    assert predicate(_fit(Job(location="Berlin"))) is False


def test_title_matches_uses_a_case_insensitive_regex():
    predicate = title_matches(r"\b(senior|staff)\b")
    assert predicate(_fit(Job(title="Senior iOS Engineer"))) is True
    assert predicate(_fit(Job(title="STAFF Engineer"))) is True
    assert predicate(_fit(Job(title="iOS Engineer"))) is False


def test_on_job_adapts_a_job_taking_function():
    predicate = on_job(lambda job: job.company == "Acme")
    assert predicate(_fit(Job(company="Acme"))) is True
    assert predicate(_fit(Job(company="Globex"))) is False


def test_combinators_compose():
    eu = in_region(Region.EU)
    assert all_of(is_remote, eu)(_fit(Job(is_remote=True, region=Region.EU))) is True
    assert all_of(is_remote, eu)(_fit(Job(is_remote=False, region=Region.EU))) is False
    assert any_of(is_remote, eu)(_fit(Job(is_remote=False, region=Region.EU))) is True
    assert not_(is_remote)(_fit(Job(is_remote=False))) is True


def test_helpers_tolerate_a_missing_evaluation():
    # FitEntry.evaluation is Optional and ReviewEntry entries reach the same
    # predicates; a fact test on a job with no evaluation is False, not a crash.
    entry = FitEntry(job=Job(), evaluation=None, summary="", pdf_bytes=b"", cv_filename="")
    assert fact("seniority", "senior")(entry) is False
    assert is_remote(entry) is False


def test_predicates_work_on_review_entries_too():
    entry = ReviewEntry(job=Job(is_remote=True), evaluation={"facts": {"seniority": "senior"}})
    assert is_remote(entry) is True
    assert fact("seniority", "senior")(entry) is True


def test_days_since_posted_counts_back_from_today():
    job = Job(date_posted=datetime.date.today() - datetime.timedelta(days=3))
    assert days_since_posted(_fit(job)) == 3


def test_days_since_posted_is_none_when_the_date_is_unknown():
    assert days_since_posted(_fit(Job())) is None
