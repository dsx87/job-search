"""Personal-profile constants and the tailored-CV content guard.

These values are byte-identical to the originals; validate_tailored_cv guards
against drift in either the fixed job timeline or the never-claim term list.
"""
import re

# Fixed reverse-chronological order of the four real jobs (matched as substrings
# of each \jobheader{...} company field). The model must never reorder these.
EXPECTED_JOB_ORDER = ["Check Point", "Applitools", "Shutterfly", "CNOGA"]

# Industries/domains Igor has never worked in, plus skills/frameworks the master
# profile says to never claim. Any of these appearing in a tailored CV is a
# fabrication (e.g. "consumer banking" injected because the employer is a bank).
FORBIDDEN_TERM_PATTERNS = [
    r"banking", r"\bbank\b", r"fintech", r"financial services",
    r"insurance", r"e-?commerce", r"\bgaming\b", r"advertising",
    r"StoreKit", r"WidgetKit", r"SwiftData", r"HealthKit", r"\bFDA\b", r"HIPAA",
]


def validate_tailored_cv(tex: str) -> list:
    """Return a list of human-readable constraint violations (empty == clean).

    Catches the two failure modes the prompt alone can't guarantee: jobs
    reordered out of fixed reverse-chronological order, and fabricated
    industries/domains or never-claim skills.
    """
    violations = []

    # 1. Job order — extract \jobheader company fields in document order, keep
    #    only the four work entries (the Education jobheader matches none), and
    #    verify their relative order matches EXPECTED_JOB_ORDER.
    headers = re.findall(r"\\jobheader\{([^}]*)\}", tex)
    seen_order = []
    for company in headers:
        for key in EXPECTED_JOB_ORDER:
            if key.lower() in company.lower():
                seen_order.append(key)
                break
    missing = [k for k in EXPECTED_JOB_ORDER if k not in seen_order]
    if missing:
        violations.append(f"missing job(s) from timeline: {', '.join(missing)}")
    elif seen_order != EXPECTED_JOB_ORDER:
        violations.append(
            f"jobs out of order: got {' → '.join(seen_order)}, "
            f"expected {' → '.join(EXPECTED_JOB_ORDER)}"
        )

    # 2. Forbidden domains / never-claim skills.
    for pattern in FORBIDDEN_TERM_PATTERNS:
        m = re.search(pattern, tex, re.IGNORECASE)
        if m:
            violations.append(f"forbidden term present: '{m.group(0)}'")

    return violations
