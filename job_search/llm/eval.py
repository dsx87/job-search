"""Job evaluation: structured fact extraction + deterministic policy.

Instead of asking the model for a direct fit verdict (whose wording could
silently change policy), we extract typed facts from the posting and apply
criteria.md deterministically in Python (audit Finding 17). The verdict is one
of "fit", "nonfit", or "uncertain"; the pipeline routes "uncertain" to a review
section rather than discarding it.
"""
from ..location.classify import is_israel_job
from ..models import coerce_job
from ..policy import apply_policy
from ..text import is_probably_english
from .facts import default_facts, extract_facts


def evaluate_job(client, criteria: str, job: dict, prompts=None) -> dict:
    """Extract facts, then apply the deterministic policy.

    Returns {"fit": bool, "reason": str, "timezone_note": str|None,
    "verdict": "fit"|"nonfit"|"uncertain", "facts": dict}. The `criteria`
    argument is retained for call-site compatibility; the policy now lives in
    job_search/policy.py (the executable form of criteria.md).

    Non-English, non-Israeli postings are rejected by the policy's language gate
    regardless of their facts, so fact extraction (an LLM call) is skipped for
    them; apply_policy still runs and returns the identical verdict/reason.
    """
    job = coerce_job(job)
    if not is_israel_job(job) and not is_probably_english(job.description):
        facts = default_facts()
    else:
        facts = extract_facts(client, job, prompts=prompts)
    decision = apply_policy(facts, job)
    return {
        "fit": decision["verdict"] == "fit",
        "reason": decision["reason"],
        "timezone_note": decision["timezone_note"],
        "verdict": decision["verdict"],
        "facts": facts,
    }
