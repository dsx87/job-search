"""Generic defaults and explicit candidate CV guards."""

from job_search.profile import (
    EXPECTED_JOB_ORDER,
    FORBIDDEN_TERM_PATTERNS,
    validate_tailored_cv,
)
from job_search.components import CandidateProfile


def _tex(order=("Example Labs", "Sample Systems"), extra=""):
    headers = "\n".join("\\jobheader{{{}}}".format(company) for company in order)
    return "\\begin{{document}}\n{}\n{}\n\\end{{document}}".format(headers, extra)


def test_default_profile_guard_is_identity_neutral():
    assert EXPECTED_JOB_ORDER == ()
    assert FORBIDDEN_TERM_PATTERNS == ()
    assert validate_tailored_cv(_tex(extra="Worked on banking systems.")) == []

    profile = CandidateProfile()
    assert profile.display_name == ""
    assert profile.base_tex_path == ""
    assert profile.cv_filename_prefix == ""
    assert profile.employer_order == ()
    assert profile.forbidden_claim_patterns == ()
    assert dict(profile.private_placeholders) == {}


def test_explicit_candidate_guard_preserves_order_and_claim_checks():
    violations = validate_tailored_cv(
        _tex(order=("Sample Systems", "Example Labs"), extra="forbidden claim"),
        expected_job_order=("Example Labs", "Sample Systems"),
        forbidden_term_patterns=(r"forbidden claim",),
    )

    assert any("out of order" in violation for violation in violations)
    assert "forbidden term present: 'forbidden claim'" in violations
