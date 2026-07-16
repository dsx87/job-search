"""Deterministic decision-table tests for the fit policy.

``apply_policy(facts, job)`` translates a normalized facts dict + a canonical
``Job`` into ``{"verdict", "reason", "timezone_note"}``. These tests exercise
each decision rule (audit order 6, part 1) and the evidence-grounding
downgrades, one case per branch. Every rule marked ``[grounded]`` in the
contract downgrades ``nonfit`` -> ``uncertain`` when its triggering fact is not
literally present in ``job.description``; grounded cases therefore place the
evidence snippet verbatim in the description, ungrounded ones omit it.
"""
from job_search.models import Job, Region
from job_search.policy import apply_policy


def _facts(**overrides):
    """A fully normalized facts dict, defaulting every field to unknown."""
    base = {
        "platform_focus": "unknown",
        "seniority": "unknown",
        "employment_type": "unknown",
        "work_arrangement": "unknown",
        "remote_geo_scope": "unknown",
        "restricted_to_countries": [],
        "offers_sponsorship": "unknown",
        "authorization_blocker": "unknown",
        "office_days_4plus": "unknown",
        "industry_crypto_web3": "unknown",
        "requires_us_hours": "unknown",
        "evidence": {},
    }
    base.update(overrides)
    return base


def test_apply_policy_result_shape():
    decision = apply_policy(
        _facts(work_arrangement="remote", remote_geo_scope="worldwide"),
        Job(description="Remote worldwide."),
    )
    assert set(decision.keys()) == {"verdict", "reason", "timezone_note"}
    assert decision["verdict"] in {"fit", "nonfit", "uncertain"}
    assert isinstance(decision["reason"], str) and decision["reason"]


# --- rule 1: crypto / Web3 --------------------------------------------

def test_crypto_grounded_nonfit():
    facts = _facts(
        industry_crypto_web3="yes",
        evidence={"industry_crypto_web3": "crypto exchange"},
    )
    decision = apply_policy(facts, Job(description="We are a crypto exchange."))
    assert decision["verdict"] == "nonfit"


def test_crypto_ungrounded_uncertain():
    facts = _facts(
        industry_crypto_web3="yes",
        evidence={"industry_crypto_web3": "crypto exchange"},
    )
    decision = apply_policy(facts, Job(description="We build iOS apps for banks."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


# --- rules 2 & 3: platform focus --------------------------------------

def test_cross_platform_grounded_nonfit():
    facts = _facts(platform_focus="cross_platform", evidence={"platform_focus": "React Native"})
    decision = apply_policy(facts, Job(description="Build apps in React Native."))
    assert decision["verdict"] == "nonfit"


def test_cross_platform_ungrounded_uncertain():
    facts = _facts(platform_focus="cross_platform", evidence={})
    decision = apply_policy(facts, Job(description="Build mobile apps."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


def test_platform_other_grounded_nonfit():
    facts = _facts(platform_focus="other", evidence={"platform_focus": "Android Kotlin"})
    decision = apply_policy(facts, Job(description="Android Kotlin developer."))
    assert decision["verdict"] == "nonfit"


def test_platform_other_ungrounded_uncertain():
    facts = _facts(platform_focus="other", evidence={})
    decision = apply_policy(facts, Job(description="Some backend role."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


# --- rule 4: seniority ------------------------------------------------

def test_junior_grounded_nonfit():
    facts = _facts(seniority="junior", evidence={"seniority": "entry-level"})
    decision = apply_policy(facts, Job(description="This is an entry-level position."))
    assert decision["verdict"] == "nonfit"


def test_junior_ungrounded_uncertain():
    facts = _facts(seniority="junior", evidence={})
    decision = apply_policy(facts, Job(description="iOS developer needed."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


# --- rule 5: Israeli roles --------------------------------------------

def test_israel_hybrid_4plus_days_grounded_nonfit():
    facts = _facts(
        work_arrangement="hybrid",
        office_days_4plus="yes",
        evidence={"office_days_4plus": "4 days per week in office"},
    )
    job = Job(location="Tel Aviv", description="Hybrid role, 4 days per week in office.")
    assert apply_policy(facts, job)["verdict"] == "nonfit"


def test_israel_hybrid_4plus_ungrounded_uncertain():
    facts = _facts(work_arrangement="hybrid", office_days_4plus="yes", evidence={})
    job = Job(location="Tel Aviv", description="Hybrid role in Tel Aviv.")
    decision = apply_policy(facts, job)
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


def test_israel_hybrid_less_than_4_days_fit():
    facts = _facts(work_arrangement="hybrid", office_days_4plus="no")
    job = Job(location="Tel Aviv", description="Hybrid, 2 days in office.")
    assert apply_policy(facts, job)["verdict"] == "fit"


def test_israel_onsite_silent_fit():
    facts = _facts(work_arrangement="onsite", office_days_4plus="unknown")
    job = Job(location="Tel Aviv", description="Onsite role.")
    assert apply_policy(facts, job)["verdict"] == "fit"


def test_israel_remote_fit():
    facts = _facts(work_arrangement="remote")
    job = Job(location="Tel Aviv", description="Remote role based in Israel.")
    assert apply_policy(facts, job)["verdict"] == "fit"


# --- rule 6: non-remote contract / freelance / part-time --------------

def test_contract_onsite_grounded_nonfit():
    facts = _facts(
        employment_type="contract", work_arrangement="onsite",
        evidence={"work_arrangement": "onsite only"},
    )
    assert apply_policy(facts, Job(description="Onsite only contract role."))["verdict"] == "nonfit"


def test_contract_onsite_ungrounded_uncertain():
    facts = _facts(employment_type="contract", work_arrangement="onsite")
    assert apply_policy(facts, Job(description="Contract role."))["verdict"] == "uncertain"


def test_freelance_hybrid_grounded_nonfit():
    facts = _facts(
        employment_type="freelance", work_arrangement="hybrid",
        evidence={"work_arrangement": "hybrid schedule"},
    )
    assert apply_policy(facts, Job(description="Hybrid schedule freelance."))["verdict"] == "nonfit"


def test_contract_unknown_arrangement_uncertain():
    facts = _facts(employment_type="contract", work_arrangement="unknown")
    assert apply_policy(facts, Job(description="Contract role."))["verdict"] == "uncertain"


def test_contract_remote_worldwide_fit():
    # remote contract skips rule 6 and falls through to the remote rule.
    facts = _facts(
        employment_type="contract", work_arrangement="remote", remote_geo_scope="worldwide"
    )
    assert apply_policy(facts, Job(description="Remote contract, worldwide."))["verdict"] == "fit"


# --- rule 7: remote ---------------------------------------------------

def test_remote_worldwide_fit():
    facts = _facts(work_arrangement="remote", remote_geo_scope="worldwide")
    assert apply_policy(facts, Job(description="Fully remote worldwide."))["verdict"] == "fit"


def test_remote_unknown_scope_fit():
    facts = _facts(work_arrangement="remote", remote_geo_scope="unknown")
    assert apply_policy(facts, Job(description="Remote role."))["verdict"] == "fit"


def test_remote_restricted_us_ca_grounded_nonfit():
    facts = _facts(
        work_arrangement="remote",
        remote_geo_scope="restricted",
        restricted_to_countries=["US"],
        evidence={"restricted_to_countries": "US residents only"},
    )
    job = Job(description="Remote but US residents only.")
    assert apply_policy(facts, job)["verdict"] == "nonfit"


def test_remote_restricted_us_ca_grounded_via_auth_blocker_nonfit():
    # grounding may come from EITHER restricted_to_countries or authorization_blocker.
    facts = _facts(
        work_arrangement="remote",
        remote_geo_scope="restricted",
        restricted_to_countries=["USA", "CANADA"],
        evidence={"authorization_blocker": "must be authorized to work in the US"},
    )
    job = Job(description="Remote; must be authorized to work in the US.")
    assert apply_policy(facts, job)["verdict"] == "nonfit"


def test_remote_restricted_us_ca_ungrounded_uncertain():
    facts = _facts(
        work_arrangement="remote",
        remote_geo_scope="restricted",
        restricted_to_countries=["US"],
        evidence={},
    )
    job = Job(description="Remote role with an unspecified residency requirement.")
    decision = apply_policy(facts, job)
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


def test_remote_restricted_non_us_uncertain():
    facts = _facts(
        work_arrangement="remote",
        remote_geo_scope="restricted",
        restricted_to_countries=["DE"],
    )
    assert apply_policy(facts, Job(description="Remote within Germany."))["verdict"] == "uncertain"


def test_remote_restricted_empty_uncertain():
    facts = _facts(
        work_arrangement="remote",
        remote_geo_scope="restricted",
        restricted_to_countries=[],
    )
    decision = apply_policy(facts, Job(description="Remote with an unclear restriction."))
    assert decision["verdict"] == "uncertain"


# --- rule 8: non-remote work-authorization blocker --------------------

def test_non_remote_auth_blocker_grounded_nonfit():
    facts = _facts(
        work_arrangement="onsite",
        authorization_blocker="yes",
        evidence={"authorization_blocker": "must be authorized to work"},
    )
    job = Job(description="Onsite; must be authorized to work in the country.")
    assert apply_policy(facts, job)["verdict"] == "nonfit"


def test_non_remote_auth_blocker_ungrounded_uncertain():
    facts = _facts(work_arrangement="onsite", authorization_blocker="yes", evidence={})
    decision = apply_policy(facts, Job(description="Onsite role."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


# --- rule 9: sponsorship ----------------------------------------------

def test_non_remote_sponsorship_grounded_fit():
    facts = _facts(
        work_arrangement="onsite",
        offers_sponsorship="yes",
        evidence={"offers_sponsorship": "visa sponsorship offered"},
    )
    job = Job(description="Onsite; visa sponsorship offered for relocation.")
    assert apply_policy(facts, job)["verdict"] == "fit"


def test_non_remote_sponsorship_ungrounded_uncertain():
    facts = _facts(work_arrangement="onsite", offers_sponsorship="yes", evidence={})
    decision = apply_policy(facts, Job(description="Onsite role."))
    assert decision["verdict"] == "uncertain"
    assert "unverif" in decision["reason"].lower()


# --- rules 10-12: EU / on-site / fallthrough --------------------------

def test_non_remote_eu_region_no_blocker_fit():
    facts = _facts(work_arrangement="onsite")
    job = Job(location="Berlin, Germany", region=Region.EU, description="Onsite role in Berlin.")
    assert apply_policy(facts, job)["verdict"] == "fit"


def test_onsite_non_eu_grounded_nonfit():
    facts = _facts(work_arrangement="onsite", evidence={"work_arrangement": "fully onsite"})
    job = Job(region=Region.UNKNOWN, description="Fully onsite role in Denver.")
    assert apply_policy(facts, job)["verdict"] == "nonfit"


def test_onsite_non_eu_ungrounded_uncertain():
    facts = _facts(work_arrangement="onsite")
    job = Job(region=Region.UNKNOWN, description="A role of some kind.")
    assert apply_policy(facts, job)["verdict"] == "uncertain"


def test_eu_role_accepts_despite_ungrounded_sponsorship_claim():
    # Finding 2: an unverified sponsorship claim must NOT downgrade an
    # otherwise-acceptable EU role to uncertain — EU acceptance comes first.
    facts = _facts(work_arrangement="onsite", offers_sponsorship="yes",
                   evidence={"offers_sponsorship": "we help you relocate"})
    job = Job(region=Region.EU, description="Berlin on-site role; no relocation wording here.")
    assert apply_policy(facts, job)["verdict"] == "fit"


def test_unknown_arrangement_non_eu_uncertain():
    facts = _facts(work_arrangement="unknown")
    job = Job(region=Region.UNKNOWN, description="A role of some kind.")
    assert apply_policy(facts, job)["verdict"] == "uncertain"


# --- timezone note (orthogonal to the verdict) ------------------------

def test_timezone_note_present_when_us_hours_required():
    facts = _facts(
        work_arrangement="remote", remote_geo_scope="worldwide", requires_us_hours="yes"
    )
    decision = apply_policy(facts, Job(description="Fully remote worldwide."))
    # the note appears but does NOT change an otherwise-fit verdict
    assert decision["verdict"] == "fit"
    assert isinstance(decision["timezone_note"], str) and decision["timezone_note"]


def test_timezone_note_absent_when_not_required():
    facts = _facts(
        work_arrangement="remote", remote_geo_scope="worldwide", requires_us_hours="no"
    )
    decision = apply_policy(facts, Job(description="Fully remote worldwide."))
    assert decision["verdict"] == "fit"
    assert decision["timezone_note"] is None
