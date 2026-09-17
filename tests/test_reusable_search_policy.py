"""Generic search and eligibility behavior, independent of the legacy profile."""
from types import SimpleNamespace
import sys
import json

from job_search.filters import run_pipeline
from job_search.llm.facts import default_facts
from job_search.llm.facts import extract_facts
from job_search.models import Job
from job_search.policy import apply_policy, evaluation_configuration_revision
from job_search.sources.jobspy_sources import JobSpySource
from job_search.sources.jobspy_sources import LinkedInGlobalSource, LinkedInIsraelSource
from job_search.sources.api_sources import WorkingNomadsSource
from job_search.sources.html_sources import RelocateMeSource
from job_search.sources import linkedin_guest
from job_search.sources.playwright_sources import SecretTelAvivSource
from job_search.sources.fetch import fetch_jobs_with_health, run_scraper, select_sources


def _search(**values):
    defaults = {
        "role_include_terms": (),
        "role_exclude_terms": (),
        "skill_include_groups": (),
        "location_exclude_terms": (),
        "relocation_regions": ("EU", "CA", "US"),
        "remote_allowed": True,
        "relocation_allowed": True,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _candidate(**values):
    defaults = {"residency_countries": (), "work_authorization_countries": ()}
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _policy(**values):
    defaults = {
        "check_order": (
            "language", "role_match", "excluded_industry", "excluded_platform_focus",
            "minimum_seniority", "local_office_attendance",
            "remote_location_residency", "nonremote_nonpermanent_employment",
            "remote_fact_residency", "nonremote_work_authorization",
            "nonremote_sponsorship", "nonremote_arrangement",
        ),
        "require_english": True,
        "local_language_exempt": True,
        "allowed_languages": ("english",),
        "excluded_industries": (),
        "excluded_platform_focuses": (),
        "rejected_seniority": (),
        "max_local_office_days": 5,
        "allow_sponsorship_override": True,
        "preferred_working_hours": (),
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_generic_search_uses_configured_role_and_skill_groups_not_ios_rules():
    search = _search(
        role_include_terms=("platform engineer",),
        skill_include_groups=(("python", "go"), ("kubernetes",)),
        location_exclude_terms=("bangalore",),
    )
    jobs = [
        Job(title="Platform Engineer", location="Berlin", description="Python and Kubernetes", is_remote=True),
        Job(title="iOS Engineer", location="Berlin", description="Swift UIKit", is_remote=True),
        Job(title="Platform Engineer", location="Bangalore", description="Python Kubernetes", is_remote=True),
    ]

    assert run_pipeline(jobs, search=search) == [jobs[0]]
    assert jobs[0].matched_skills == ["kubernetes", "python"]


def test_generic_skill_aliases_are_explicit_in_config_not_inherited_from_ios_profile():
    job = Job(title="Data Engineer", description="CoreData pipelines", is_remote=True)
    without_alias = _search(
        role_include_terms=("data engineer",), skill_include_groups=(("core data",),),
    )
    with_alias = _search(
        role_include_terms=("data engineer",), skill_include_groups=(("core data", "coredata"),),
    )

    assert run_pipeline([job], search=without_alias) == []
    assert run_pipeline([job], search=with_alias) == [job]


def test_generic_token_matching_supports_punctuation_skills():
    search = _search(role_include_terms=("systems engineer",), skill_include_groups=(("c++", "c#"),))
    job = Job(title="Systems Engineer", description="Modern C++ services", is_remote=True)

    assert run_pipeline([job], search=search) == [job]


def test_generic_source_default_excludes_role_specialized_boards_but_can_enable_them():
    generic = select_sources(generic=True)

    assert not {"arc", "mobile.career", "jobscroller", "secrettelaviv", "linkedin-israel"}.intersection(generic)
    assert "arc" in select_sources(("arc",), generic=True)


def test_query_sources_report_skip_when_generic_query_configuration_is_empty():
    search = SimpleNamespace(search_terms=(), query_locations=())
    sources = (
        JobSpySource(), LinkedInGlobalSource(), LinkedInIsraelSource(),
        WorkingNomadsSource(), RelocateMeSource(),
    )

    for source in sources:
        source.search = search
        assert source.fetch() == []
        assert "configured search requires" in source._skip_detail


def test_configured_location_exclusion_ignores_a_company_team_mention():
    search = _search(
        role_include_terms=("platform engineer",),
        location_exclude_terms=("bangalore",),
    )
    job = Job(
        title="Platform Engineer", location="Berlin, Germany", is_remote=True,
        description="You will collaborate with our Bangalore platform team.",
    )

    assert run_pipeline([job], search=search) == [job]


def test_authorized_local_job_survives_generic_filter_before_relocation_gate():
    search = _search(remote_allowed=False, relocation_allowed=False)
    candidate = _candidate(residency_countries=("IL",), work_authorization_countries=("IL",))
    job = Job(title="Platform Engineer", location="Tel Aviv, Israel", description="Onsite", is_remote=False)

    assert run_pipeline([job], search=search, candidate=candidate) == [job]


def test_residency_without_explicit_work_authorization_does_not_pass_local_filter():
    search = _search(remote_allowed=False, relocation_allowed=False)
    candidate = _candidate(residency_countries=("IL",), work_authorization_countries=())
    job = Job(title="Platform Engineer", location="Tel Aviv, Israel", description="Onsite", is_remote=False)

    assert run_pipeline([job], search=search, candidate=candidate) == []


def test_fetch_threads_candidate_to_the_generic_local_filter(monkeypatch):
    job = Job(title="Platform Engineer", location="Tel Aviv, Israel", description="Onsite", is_remote=False)

    class Source:
        _skip_detail = ""
        _attempts = 0
        _failures = ()
        _timeout_detail = ""

        def fetch(self, verbose=False):
            return [job]

    monkeypatch.setattr("job_search.sources.fetch.ALL_SOURCES", {"fixture": Source})
    search = _search(remote_allowed=False, relocation_allowed=False)
    report = fetch_jobs_with_health(
        source_names=["fixture"], search=search,
        candidate=_candidate(residency_countries=("IL",), work_authorization_countries=("IL",)),
    )

    assert list(report.jobs) == [job]


def test_run_scraper_forwards_an_explicit_scrape_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr("job_search.sources.fetch.ALL_SOURCES", {"fixture": object})
    monkeypatch.setattr("job_search.sources.fetch.format_source_health", lambda _report: "")

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(has_usable_source=True, jobs=())

    monkeypatch.setattr("job_search.sources.fetch.fetch_jobs_with_health", fake_fetch)

    assert run_scraper(source_names=["fixture"], as_json=True, budget_seconds=17) == 0
    assert captured["budget_seconds"] == 17


def test_explicit_filter_age_and_empty_relocation_regions_override_search_defaults():
    search = _search(max_age_days=20, relocation_regions=("EU",))
    old_remote = Job(title="Platform Engineer", description="Remote", is_remote=True)
    old_remote.date_posted = __import__("datetime").date.today() - __import__("datetime").timedelta(days=25)
    onsite = Job(title="Platform Engineer", location="Berlin, Germany", description="Visa sponsorship available", is_remote=False)

    assert run_pipeline([old_remote], max_age_days=30, search=search) == [old_remote]
    assert run_pipeline([onsite], relocation_regions=set(), search=search) == []


def test_remote_country_match_does_not_fall_back_to_its_broader_region():
    candidate = _candidate(residency_countries=("FR",))
    facts = default_facts()
    facts["work_arrangement"] = "remote"

    decision = apply_policy(
        facts, Job(location="Remote - Germany", is_remote=True),
        candidate=candidate, policy=_policy(require_english=False),
    )

    assert decision["verdict"] == "nonfit"


def test_remote_ambiguous_scope_is_uncertain_but_worldwide_is_unrestricted():
    candidate = _candidate(residency_countries=("IL",))
    facts = default_facts()
    facts["work_arrangement"] = "remote"
    policy = _policy(require_english=False)

    assert apply_policy(facts, Job(location="Remote - EMEA", is_remote=True), candidate=candidate, policy=policy)["verdict"] == "uncertain"
    assert apply_policy(facts, Job(location="Remote - Germany / Worldwide", is_remote=True), candidate=candidate, policy=policy)["verdict"] == "fit"


def test_remote_known_eligible_country_wins_over_an_unknown_alternative():
    facts = default_facts()
    facts["work_arrangement"] = "remote"

    assert apply_policy(
        facts, Job(location="Remote - Germany / EMEA", is_remote=True),
        candidate=_candidate(residency_countries=("DE",)), policy=_policy(require_english=False),
    )["verdict"] == "fit"


def test_remote_worldwide_words_with_a_country_qualifier_are_not_unrestricted():
    facts = default_facts()
    facts["work_arrangement"] = "remote"
    candidate = _candidate(residency_countries=("IL",))
    policy = _policy(require_english=False)

    for location in (
        "Remote - Anywhere in the United States",
        "Work from anywhere in Germany",
        "Global - US only",
        "Remote worldwide except Israel",
    ):
        assert apply_policy(
            facts, Job(location=location, is_remote=True), candidate=candidate, policy=policy,
        )["verdict"] != "fit"


def test_global_us_only_preserves_the_iso_code_for_eligible_residency():
    facts = default_facts()
    facts["work_arrangement"] = "remote"
    job = Job(location="Global - US only", is_remote=True)
    policy = _policy(require_english=False)

    assert apply_policy(facts, job, candidate=_candidate(residency_countries=("US",)), policy=policy)["verdict"] == "fit"
    assert apply_policy(facts, job, candidate=_candidate(residency_countries=("IL",)), policy=policy)["verdict"] == "nonfit"


def test_explicitly_authorized_local_unknown_arrangement_is_fit_after_generic_checks():
    candidate = _candidate(residency_countries=("IL",), work_authorization_countries=("IL",))
    facts = default_facts()
    facts.update({"role_match": "yes", "matched_required_skills": ["swift"]})
    search = _search(role_include_terms=("ios",), skill_include_groups=(("swift",),))

    assert apply_policy(
        facts, Job(location="Tel Aviv, Israel", description="iOS role."),
        candidate=candidate, policy=_policy(require_english=False), search=search,
    )["verdict"] == "fit"


def test_unknown_local_arrangement_requires_residency_and_authorization_for_the_same_country():
    facts = default_facts()
    job = Job(location="Berlin, Germany / Paris, France", description="Local role.")
    policy = _policy(require_english=False)

    assert apply_policy(
        facts, job,
        candidate=_candidate(residency_countries=("DE",), work_authorization_countries=("FR",)),
        policy=policy,
    )["verdict"] == "uncertain"
    assert apply_policy(
        facts, job,
        candidate=_candidate(residency_countries=("DE",), work_authorization_countries=("DE",)),
        policy=policy,
    )["verdict"] == "fit"


def test_explicitly_authorized_local_contract_keeps_the_legacy_local_exception():
    facts = default_facts()
    facts.update({
        "work_arrangement": "onsite",
        "employment_type": "contract",
        "evidence": {"work_arrangement": "onsite contract"},
    })
    job = Job(location="Tel Aviv, Israel", description="Onsite contract role.")

    assert apply_policy(
        facts, job,
        candidate=_candidate(residency_countries=("IL",), work_authorization_countries=("IL",)),
        policy=_policy(require_english=False),
    )["verdict"] == "fit"


def test_restricted_country_names_from_extraction_are_normalized_for_policy():
    class Client:
        def generate(self, *_args, **_kwargs):
            return json.dumps({
                "work_arrangement": "remote",
                "remote_geo_scope": "restricted",
                "restricted_to_countries": ["Germany"],
                "evidence": [
                    {"field": "restricted_to_countries", "snippet": "Germany residents only"},
                ],
            })

    job = Job(
        location="Remote", is_remote=True,
        description="This remote role is for Germany residents only.",
    )
    facts = extract_facts(Client(), job, search=_search(), policy=_policy(require_english=False))

    assert apply_policy(facts, job, candidate=_candidate(residency_countries=("DE",)), policy=_policy(require_english=False))["verdict"] == "fit"
    assert apply_policy(facts, job, candidate=_candidate(residency_countries=("FR",)), policy=_policy(require_english=False))["verdict"] == "nonfit"


def test_missing_language_fact_is_uncertain_for_multiple_allowed_languages():
    facts = default_facts()
    facts["work_arrangement"] = "remote"
    decision = apply_policy(
        facts,
        Job(location="Remote", is_remote=True, description="日本語のみの求人です。"),
        candidate=_candidate(),
        policy=_policy(allowed_languages=("english", "hebrew")),
    )

    assert decision["verdict"] == "uncertain"


def test_lead_seniority_is_extractable_and_enforced_when_configured():
    class Client:
        def generate(self, *_args, **_kwargs):
            return json.dumps({"seniority": "lead", "evidence": {"seniority": "Lead Platform Engineer"}})

    job = Job(description="Lead Platform Engineer")
    facts = extract_facts(Client(), job)

    decision = apply_policy(
        facts, job,
        candidate=_candidate(), policy=_policy(require_english=False, rejected_seniority=("lead",)),
    )

    assert decision["verdict"] == "nonfit"


def test_default_facts_has_generic_role_and_skill_fields():
    facts = default_facts()

    assert facts["role_match"] == "unknown"
    assert facts["matched_role_terms"] == []
    assert facts["matched_required_skills"] == []


def test_remote_location_eligibility_uses_candidate_residency():
    candidate = _candidate(residency_countries=("DE",), work_authorization_countries=("DE",))
    policy = _policy(require_english=False)
    facts = default_facts()
    facts["work_arrangement"] = "remote"

    eligible = apply_policy(facts, Job(location="Remote - Germany", is_remote=True), candidate=candidate, policy=policy)
    unavailable = apply_policy(facts, Job(location="Remote - United Kingdom", is_remote=True), candidate=candidate, policy=policy)

    assert eligible["verdict"] == "fit"
    assert unavailable["verdict"] == "nonfit"


def test_ungrounded_authorization_blocker_is_uncertain_for_generic_candidate():
    candidate = _candidate(residency_countries=("DE",), work_authorization_countries=())
    policy = _policy(require_english=False)
    facts = default_facts()
    facts.update({"work_arrangement": "onsite", "authorization_blocker": "yes"})

    decision = apply_policy(facts, Job(location="Berlin, Germany", description="Onsite role."), candidate=candidate, policy=policy)

    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


def test_grounded_remote_authorization_requirement_does_not_infer_permission_from_residency():
    facts = default_facts()
    facts.update({
        "work_arrangement": "remote",
        "remote_geo_scope": "restricted",
        "restricted_to_countries": ["US"],
        "authorization_blocker": "yes",
        "evidence": {"authorization_blocker": "must be authorized to work in the US"},
    })
    job = Job(location="Remote", is_remote=True, description="Applicants must be authorized to work in the US.")
    policy = _policy(require_english=False)

    assert apply_policy(
        facts, job, candidate=_candidate(residency_countries=("US",)), policy=policy,
    )["verdict"] == "nonfit"
    assert apply_policy(
        facts, job,
        candidate=_candidate(residency_countries=("US",), work_authorization_countries=("US",)),
        policy=policy,
    )["verdict"] == "fit"


def test_jobspy_uses_generic_search_terms_locations_and_result_limit(monkeypatch):
    calls = []

    class Frame:
        def iterrows(self):
            return iter(())

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return Frame()

    monkeypatch.setitem(sys.modules, "jobspy", SimpleNamespace(scrape_jobs=scrape_jobs))
    source = JobSpySource()
    source.search = SimpleNamespace(
        search_terms=("platform engineer",),
        query_locations=("Germany", "Netherlands"),
        results_per_query=7,
    )

    assert source.fetch() == []
    assert [(call["search_term"], call["location"], call["country_indeed"], call["results_wanted"]) for call in calls] == [
        ("platform engineer", "Germany", "Germany", 7),
        ("platform engineer", "Netherlands", "Netherlands", 7),
    ]


def test_jobspy_uses_google_only_for_a_region_without_indeed_country(monkeypatch):
    calls = []

    class Frame:
        def iterrows(self):
            return iter(())

    monkeypatch.setitem(
        sys.modules, "jobspy", SimpleNamespace(
            scrape_jobs=lambda **kwargs: (calls.append(kwargs) or Frame()),
        ),
    )
    source = JobSpySource()
    source.search = SimpleNamespace(
        search_terms=("data engineer",), query_locations=("European Union",), results_per_query=5,
    )

    assert source.fetch() == []
    assert calls == [{
        "site_name": ["google"], "location": "European Union",
        "search_term": "data engineer", "results_wanted": 5, "hours_old": 720,
    }]


def test_generic_industry_rejection_requires_grounded_evidence():
    policy = _policy(require_english=False, excluded_industries=("healthcare",))
    facts = default_facts()
    facts.update({"industries": ["healthcare"], "evidence": {"industries": "healthcare software"}})

    decision = apply_policy(facts, Job(description="We make consumer applications."), candidate=_candidate(), policy=policy)

    assert decision["verdict"] == "uncertain"


def test_generic_allowed_language_uses_grounded_extracted_language():
    policy = _policy(require_english=False, allowed_languages=("french",))
    facts = default_facts()
    facts.update({"description_language": "german", "evidence": {"description_language": "Wir suchen"}})

    decision = apply_policy(facts, Job(description="Wir suchen eine Entwicklerin."), candidate=_candidate(), policy=policy)

    assert decision["verdict"] == "nonfit"


def test_generic_local_office_limit_uses_exact_days_and_grounding():
    policy = _policy(require_english=False, max_local_office_days=2)
    facts = default_facts()
    facts.update({"office_days_per_week": "3", "evidence": {"office_days_per_week": "3 days in office"}})

    decision = apply_policy(
        facts, Job(location="Berlin, Germany", description="3 days in office."),
        candidate=_candidate(residency_countries=("DE",)), policy=policy,
    )

    assert decision["verdict"] == "nonfit"


def test_generic_role_skill_check_defers_when_configured_skill_is_not_extracted():
    search = _search(role_include_terms=("data engineer",), skill_include_groups=(("python",),))
    facts = default_facts()
    facts.update({"role_match": "yes", "matched_role_terms": ["data engineer"]})

    decision = apply_policy(facts, Job(description="Data engineering role."), candidate=_candidate(), policy=_policy(require_english=False), search=search)

    assert decision["verdict"] == "uncertain"


def test_evaluation_revision_changes_for_eligibility_but_not_cv_only_options():
    search = _search()
    policy = _policy()
    candidate = _candidate(residency_countries=("DE",), work_authorization_countries=("DE",))
    cv_variant = SimpleNamespace(
        residency_countries=("DE",), work_authorization_countries=("DE",),
        max_pages=1, display_name="Different Candidate",
    )

    baseline = evaluation_configuration_revision(search, candidate, policy)
    assert evaluation_configuration_revision(search, cv_variant, policy) == baseline
    assert evaluation_configuration_revision(search, _candidate(residency_countries=("FR",)), policy) != baseline


def test_transitional_ios_israel_policy_preserves_recorded_posting_verdicts():
    """Golden compatibility cases from the pre-reusable evaluator contract."""
    transitional_search = _search(
        role_include_terms=("ios", "macos"),
        skill_include_groups=(("swift", "objective-c"),),
    )
    transitional_candidate = _candidate(
        residency_countries=("IL",), work_authorization_countries=("IL",),
    )
    transitional_policy = _policy(
        excluded_industries=("crypto_web3",),
        excluded_platform_focuses=("cross_platform", "other"),
        rejected_seniority=("junior",),
        max_local_office_days=3,
    )
    cases = (
        (
            Job(location="Remote", is_remote=True, description="Remote worldwide iOS role."),
            {"work_arrangement": "remote", "remote_geo_scope": "worldwide", "role_match": "yes", "matched_required_skills": ["swift"]},
            "fit",
        ),
        (
            Job(location="Tel Aviv, Israel", description="Onsite iOS role with two office days."),
            {"work_arrangement": "onsite", "office_days_per_week": "2", "role_match": "yes", "matched_required_skills": ["swift"]},
            "fit",
        ),
        (
            Job(location="Remote - United Kingdom", is_remote=True, description="Remote iOS role."),
            {"work_arrangement": "remote", "role_match": "yes", "matched_required_skills": ["swift"]},
            "nonfit",
        ),
    )

    for job, values, expected in cases:
        facts = default_facts()
        facts.update(values)
        assert apply_policy(
            facts, job, candidate=transitional_candidate,
            policy=transitional_policy, search=transitional_search,
        )["verdict"] == expected


def test_transitional_ios_israel_config_matches_legacy_policy_golden_verdicts():
    search = _search(role_include_terms=("ios",), skill_include_groups=(("swift",),))
    candidate = _candidate(residency_countries=("IL",), work_authorization_countries=("IL",))
    policy = _policy(
        excluded_industries=("crypto_web3",),
        excluded_platform_focuses=("cross_platform", "other"),
        rejected_seniority=("junior",),
        max_local_office_days=3,
    )
    cases = (
        (Job(location="Remote", is_remote=True, description="Remote iOS role worldwide."), {"work_arrangement": "remote", "remote_geo_scope": "worldwide", "role_match": "yes", "matched_required_skills": ["swift"]}),
        (Job(location="Tel Aviv, Israel", description="iOS role."), {"role_match": "yes", "matched_required_skills": ["swift"]}),
        (Job(location="Remote - United Kingdom", is_remote=True, description="Remote iOS role."), {"work_arrangement": "remote", "role_match": "yes", "matched_required_skills": ["swift"]}),
    )

    for job, values in cases:
        facts = default_facts()
        facts.update(values)
        assert apply_policy(facts, job)["verdict"] == apply_policy(
            facts, job, candidate=candidate, policy=policy, search=search,
        )["verdict"]


def test_working_nomads_payload_uses_generic_search_terms():
    source = WorkingNomadsSource()
    source.search = SimpleNamespace(search_terms=("data engineer", "python"), results_per_query=23)

    payload = source.payload()
    query = payload["query"]["bool"]["must"]["query_string"]["query"]

    assert query == '"data engineer" OR "python"'
    assert payload["size"] == 23


def test_relocate_me_uses_generic_search_terms(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "job_search.sources.html_sources.http_request",
        lambda _url, params=None: (calls.append(params) or (200, "")),
    )
    source = RelocateMeSource()
    source.search = SimpleNamespace(search_terms=("data engineer",))

    assert source.fetch() == []
    assert calls == [{"q": "data engineer"}]


def test_linkedin_guest_uses_generic_queries_locations_and_result_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(linkedin_guest, "POLITE_DELAY", 0)
    monkeypatch.setattr(
        linkedin_guest, "http_request",
        lambda _url, params=None, **_kwargs: (calls.append(params) or (200, "")),
    )
    source = linkedin_guest.LinkedInGuestSource()
    source.search = SimpleNamespace(
        search_terms=("data engineer",), query_locations=("France",), results_per_query=11,
    )

    assert source.fetch() == []
    assert calls == [{"keywords": "data engineer", "location": "France", "start": 0}]


def test_secret_tel_aviv_uses_generic_search_terms(monkeypatch):
    urls = []

    class Page:
        def goto(self, url, **_kwargs):
            urls.append(url)

        def wait_for_timeout(self, _milliseconds):
            pass

        def content(self):
            return ""

        def close(self):
            pass

    class Context:
        def new_page(self):
            return Page()

    class Browser:
        def new_context(self, **_kwargs):
            return Context()

        def close(self):
            pass

    class Playwright:
        chromium = SimpleNamespace(launch=lambda **_kwargs: Browser())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=lambda: Playwright()))
    source = SecretTelAvivSource()
    source.search = SimpleNamespace(search_terms=("data engineer",))

    assert source.fetch() == []
    assert urls == ["https://jobs.secrettelaviv.com/list/find/?q=data+engineer"]


def test_custom_fact_prompt_is_preserved_when_generic_context_is_added():
    prompts = SimpleNamespace(fact_extraction=lambda _job: "CUSTOM FACT PROMPT")
    seen = []

    class Client:
        def generate(self, prompt, **_kwargs):
            seen.append(prompt)
            return "{}"

    extract_facts(
        Client(), Job(description="A posting."), prompts=prompts,
        search=_search(role_include_terms=("data engineer",)),
        policy=_policy(excluded_industries=("healthcare",)),
    )

    assert "CUSTOM FACT PROMPT" in seen[0]
    assert "data engineer" in seen[0]
    assert "healthcare" in seen[0]


def test_builtin_fact_prompt_adds_generic_context_once():
    seen = []

    class Client:
        def generate(self, prompt, **_kwargs):
            seen.append(prompt)
            return "{}"

    extract_facts(Client(), Job(description="A posting."), search=_search(role_include_terms=("data engineer",)), policy=_policy())

    assert seen[0].count("## Reusable Candidate Context") == 1
