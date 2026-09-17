"""Candidate-configurable tailored-CV content guard.

The package carries no candidate identity. Callers that need timeline or claim
constraints pass them explicitly from their configured candidate profile.
"""
import re

# Compatibility names for callers that omit a profile. They are deliberately
# empty: a reusable package must not impose one person's history or exclusions.
EXPECTED_JOB_ORDER = ()
FORBIDDEN_TERM_PATTERNS = ()


def validate_tailored_cv(
    tex: str, expected_job_order=None, forbidden_term_patterns=None
) -> list:
    """Return a list of human-readable constraint violations (empty == clean).

    Candidate-specific constraints are optional and supplied by the caller.
    """
    violations = []

    # 1. Job order — extract \jobheader company fields in document order, keep
    #    configured work entries, and verify their relative order.
    expected_job_order = list(
        EXPECTED_JOB_ORDER if expected_job_order is None else expected_job_order
    )
    forbidden_term_patterns = list(
        FORBIDDEN_TERM_PATTERNS
        if forbidden_term_patterns is None
        else forbidden_term_patterns
    )
    headers = re.findall(r"\\jobheader\{([^}]*)\}", tex)
    seen_order = []
    for company in headers:
        for key in expected_job_order:
            if key.lower() in company.lower():
                seen_order.append(key)
                break
    missing = [k for k in expected_job_order if k not in seen_order]
    if missing:
        violations.append(f"missing job(s) from timeline: {', '.join(missing)}")
    elif seen_order != expected_job_order:
        violations.append(
            f"jobs out of order: got {' → '.join(seen_order)}, "
            f"expected {' → '.join(expected_job_order)}"
        )

    # 2. Forbidden domains / never-claim skills.
    for pattern in forbidden_term_patterns:
        m = re.search(pattern, tex, re.IGNORECASE)
        if m:
            violations.append(f"forbidden term present: '{m.group(0)}'")

    return violations
