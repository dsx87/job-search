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


# ── group_entries ─────────────────────────────────────────────────────────────

from job_search.digest.sections import OTHER_SECTION, group_entries  # noqa: E402


def _remote_section():
    return Section("Remote", "🌍", match=is_remote)


def _acme_section(applies_to=("fits",)):
    return Section("Acme", "🏢", applies_to=applies_to,
                   match=on_job(lambda job: job.company == "Acme"))


def test_no_sections_means_no_groups_so_the_caller_renders_flat():
    entries = [_fit(Job(is_remote=True))]
    groups, warnings = group_entries(entries, (), "fits")
    assert groups == []
    assert warnings == []


def test_each_entry_lands_in_the_first_section_it_matches():
    # A remote Acme job matches both; Acme is listed first, so it wins.
    both = _fit(Job(company="Acme", is_remote=True))
    remote_only = _fit(Job(company="Globex", is_remote=True))
    sections = (_acme_section(), _remote_section())

    groups, _warnings = group_entries([both, remote_only], sections, "fits")

    assert [section.name for section, _bucket in groups] == ["Acme", "Remote"]
    assert groups[0][1] == [both]
    assert groups[1][1] == [remote_only]


def test_unmatched_entries_go_to_a_trailing_other_group():
    matched = _fit(Job(is_remote=True))
    unmatched = _fit(Job(is_remote=False))

    groups, _warnings = group_entries([matched, unmatched], (_remote_section(),), "fits")

    assert groups[-1][0] is OTHER_SECTION
    assert groups[-1][1] == [unmatched]


def test_a_section_without_a_match_is_an_explicit_catch_all():
    unmatched = _fit(Job(is_remote=False))
    sections = (_remote_section(), Section("Everything else", "📋"))

    groups, _warnings = group_entries([unmatched], sections, "fits")

    # The user's own catch-all absorbs it, so no implicit Other group appears.
    assert [section.name for section, _bucket in groups] == ["Everything else"]


def test_empty_groups_are_dropped():
    groups, _warnings = group_entries([_fit(Job(is_remote=True))],
                                      (_remote_section(), _acme_section()), "fits")
    assert [section.name for section, _bucket in groups] == ["Remote"]


def test_order_within_a_group_follows_the_input_order():
    first = _fit(Job(title="A", is_remote=True))
    second = _fit(Job(title="B", is_remote=True))
    groups, _warnings = group_entries([first, second], (_remote_section(),), "fits")
    assert groups[0][1] == [first, second]


def test_applies_to_selects_which_lists_a_section_groups():
    entry = _fit(Job(company="Acme"))
    fits_only = (_acme_section(applies_to=("fits",)),)

    assert group_entries([entry], fits_only, "fits")[0] != []
    assert group_entries([entry], fits_only, "review")[0] == []

    both = (_acme_section(applies_to=("fits", "review")),)
    assert group_entries([entry], both, "review")[0] != []


def test_a_raising_predicate_falls_through_and_is_reported_once():
    def boom(_entry):
        raise AttributeError("no such field")

    entries = [_fit(Job(is_remote=True)), _fit(Job(is_remote=True))]
    sections = (Section("Broken", "💥", match=boom), _remote_section())

    groups, warnings = group_entries(entries, sections, "fits")

    # Both entries skipped the broken section and landed in the next match.
    assert [section.name for section, _bucket in groups] == ["Remote"]
    assert len(groups[0][1]) == 2
    # Two entries hit the same failure; it is reported once, naming the section.
    assert len(warnings) == 1
    assert "Broken" in warnings[0]
    assert "no such field" in warnings[0]


def test_a_predicate_raising_a_different_message_per_entry_is_still_reported_once():
    # The dedup key must not include str(exc): a predicate that embeds per-entry
    # data in its message (e.g. ValueError(entry.job.title)) would otherwise
    # produce one warning per entry and bury the rest of the digest.
    def boom(entry):
        raise ValueError(entry.job.title)

    entries = [_fit(Job(title="A", is_remote=True)), _fit(Job(title="B", is_remote=True))]
    sections = (Section("Broken", "💥", match=boom), _remote_section())

    groups, warnings = group_entries(entries, sections, "fits")

    assert [section.name for section, _bucket in groups] == ["Remote"]
    assert len(groups[0][1]) == 2
    assert len(warnings) == 1


def test_a_predicate_raising_SystemExit_falls_through_instead_of_killing_the_run():
    def boom(_entry):
        raise SystemExit(3)

    entries = [_fit(Job(is_remote=True))]
    sections = (Section("Broken", "💥", match=boom), _remote_section())

    groups, warnings = group_entries(entries, sections, "fits")

    assert [section.name for section, _bucket in groups] == ["Remote"]
    assert len(warnings) == 1
    assert "SystemExit" in warnings[0]
