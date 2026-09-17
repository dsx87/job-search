"""Executable compatibility corpus for the explicit transitional profile."""
import copy
import re
from pathlib import Path

from job_search.config import PipelineConfig
from job_search.filters.keywords import match_configured_keywords
from job_search.llm.facts import default_facts
from job_search.models import Job
from job_search.policy import apply_policy
from job_search.text import is_probably_english


_FIXTURE = Path(__file__).parent / "fixtures" / "transitional_policy.toml"


def _facts(**values):
    facts = default_facts()
    facts.update(values)
    return facts


def _adapt_legacy_facts(facts, job, config):
    """Derive only literal configured matches and legacy-equivalent facts.

    This models fields that the new extractor is explicitly asked to report. It
    does not infer an industry, office schedule, or target role from semantics.
    """
    adapted = copy.deepcopy(facts)
    evidence = dict(adapted.get("evidence") or {})
    text = "{} {}".format(job.title, job.description)
    roles = match_configured_keywords(text.lower(), config.search.role_include_terms)
    if roles:
        adapted["role_match"] = "yes"
        adapted["matched_role_terms"] = roles
        evidence["role_match"] = roles[0]
    elif adapted.get("platform_focus") in ("cross_platform", "other"):
        adapted["role_match"] = "no"
        evidence["role_match"] = evidence.get("platform_focus", "")

    skills = []
    for group in config.search.skill_include_groups:
        skills.extend(match_configured_keywords(text.lower(), group))
    if skills:
        adapted["matched_required_skills"] = sorted(set(skills))

    if adapted.get("industry_crypto_web3") == "yes":
        adapted["industries"] = ["crypto_web3"]
        evidence["industries"] = evidence.get("industry_crypto_web3", "")

    office = re.search(r"\b([0-7])\s+days?\s+(?:per\s+week\s+)?in\s+(?:the\s+)?office\b", job.description, re.I)
    if office:
        adapted["office_days_per_week"] = office.group(1)
        evidence["office_days_per_week"] = office.group(0)
    elif adapted.get("office_days_4plus") == "yes":
        adapted["office_days_per_week"] = "4"
        evidence["office_days_per_week"] = evidence.get("office_days_4plus", "")

    if is_probably_english(job.description):
        adapted["description_language"] = "english"
    adapted["evidence"] = evidence
    return adapted


def _load_transitional(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_SETTINGS_FILE", str(_FIXTURE))
    return PipelineConfig.from_env()


def test_transitional_toml_matches_the_recorded_legacy_policy_matrix(monkeypatch):
    config = _load_transitional(monkeypatch)
    cases = (
        (
            "remote_worldwide", Job(title="iOS Engineer", location="Remote", is_remote=True, description="Swift iOS work, fully remote worldwide."),
            _facts(work_arrangement="remote", remote_geo_scope="worldwide"), "fit",
        ),
        (
            "local_contract", Job(title="iOS Engineer", location="Tel Aviv, Israel", description="Onsite contract work with Swift."),
            _facts(work_arrangement="onsite", employment_type="contract", evidence={"work_arrangement": "Onsite contract"}), "fit",
        ),
        (
            "remote_geography", Job(title="iOS Engineer", location="Remote - United Kingdom", is_remote=True, description="Swift iOS remote work."),
            _facts(work_arrangement="remote"), "nonfit",
        ),
        (
            "sponsorship", Job(title="iOS Engineer", location="Berlin, Germany", description="Onsite Swift iOS work; visa sponsorship offered."),
            _facts(work_arrangement="onsite", offers_sponsorship="yes", evidence={"offers_sponsorship": "visa sponsorship offered"}), "fit",
        ),
        (
            "industry", Job(title="iOS Engineer", location="Remote", is_remote=True, description="Swift iOS role at a crypto exchange."),
            _facts(work_arrangement="remote", industry_crypto_web3="yes", evidence={"industry_crypto_web3": "crypto exchange"}), "nonfit",
        ),
        (
            "platform", Job(title="iOS Engineer", location="Remote", is_remote=True, description="Swift iOS and React Native work."),
            _facts(work_arrangement="remote", platform_focus="cross_platform", evidence={"platform_focus": "React Native"}), "nonfit",
        ),
        (
            "seniority", Job(title="iOS Engineer", location="Remote", is_remote=True, description="Swift iOS entry-level role."),
            _facts(work_arrangement="remote", seniority="junior", evidence={"seniority": "entry-level"}), "nonfit",
        ),
        (
            "office", Job(title="iOS Engineer", location="Tel Aviv, Israel", description="Swift iOS hybrid role, 4 days per week in office."),
            _facts(work_arrangement="hybrid", office_days_4plus="yes", evidence={"office_days_4plus": "4 days per week in office"}), "nonfit",
        ),
        (
            "ungrounded_industry", Job(title="iOS Engineer", location="Remote", is_remote=True, description="Swift iOS consumer application work."),
            _facts(work_arrangement="remote", industry_crypto_web3="yes", evidence={"industry_crypto_web3": "crypto exchange"}), "uncertain",
        ),
    )

    for name, job, facts, expected in cases:
        legacy = apply_policy(facts, job)
        generic = apply_policy(
            _adapt_legacy_facts(facts, job, config), job,
            search=config.search, candidate=config.candidate, policy=config.policy,
        )
        assert legacy["verdict"] == expected, name
        assert generic["verdict"] == expected, name


def test_unknown_non_english_language_is_an_intentional_generic_review_case(monkeypatch):
    config = _load_transitional(monkeypatch)
    job = Job(title="iOS Engineer", location="Remote", is_remote=True, description="משרת iOS עם Swift לעבודה מרחוק.")
    facts = _facts(work_arrangement="remote", remote_geo_scope="worldwide")

    assert apply_policy(facts, job)["verdict"] == "nonfit"
    adapted = _adapt_legacy_facts(facts, job, config)
    assert adapted["description_language"] == ""
    assert apply_policy(
        adapted, job, search=config.search, candidate=config.candidate, policy=config.policy,
    )["verdict"] == "uncertain"
