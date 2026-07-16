"""Job evaluation: structured fact extraction + deterministic policy.

Instead of asking the model for a direct fit verdict (whose wording could
silently change policy), we extract typed facts from the posting and apply
criteria.md deterministically in Python (audit Finding 17). The verdict is one
of "fit", "nonfit", or "uncertain"; the pipeline routes "uncertain" to a review
section rather than discarding it.
"""
from ..models import coerce_job
from ..policy import apply_policy
from .facts import extract_facts


def evaluate_job(client, criteria: str, job: dict) -> dict:
    """Extract facts, then apply the deterministic policy.

    Returns {"fit": bool, "reason": str, "timezone_note": str|None,
    "verdict": "fit"|"nonfit"|"uncertain", "facts": dict}. The `criteria`
    argument is retained for call-site compatibility; the policy now lives in
    job_search/policy.py (the executable form of criteria.md).
    """
    job = coerce_job(job)
    facts = extract_facts(client, job)
    decision = apply_policy(facts, job)
    return {
        "fit": decision["verdict"] == "fit",
        "reason": decision["reason"],
        "timezone_note": decision["timezone_note"],
        "verdict": decision["verdict"],
        "facts": facts,
    }
