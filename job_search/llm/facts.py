"""Structured, schema-constrained fact extraction from a job posting.

The model is asked ONLY to report what the posting states as typed facts (never
to judge fit); the deterministic policy in job_search/policy.py then applies
criteria.md to those facts. This keeps decisions auditable and prevents prompt
wording from silently changing policy (audit Finding 17).
"""
import json

from ..models import coerce_job
from ..text import section_aware_excerpt

_ENUMS = {
    "platform_focus": ("ios_macos", "cross_platform", "other", "unknown"),
    "seniority": ("senior", "mid", "junior", "unknown"),
    "employment_type": ("full_time", "part_time", "contract", "freelance", "internship", "unknown"),
    "work_arrangement": ("remote", "hybrid", "onsite", "unknown"),
    "remote_geo_scope": ("worldwide", "restricted", "unknown"),
    "offers_sponsorship": ("yes", "no", "unknown"),
    "authorization_blocker": ("yes", "no", "unknown"),
    "office_days_4plus": ("yes", "no", "unknown"),
    "industry_crypto_web3": ("yes", "no", "unknown"),
    "requires_us_hours": ("yes", "no", "unknown"),
}

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        **{field: {"type": "string", "enum": list(values)} for field, values in _ENUMS.items()},
        "restricted_to_countries": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "snippet": {"type": "string"},
                },
            },
        },
    },
}


def _enum(raw, field):
    value = str(raw.get(field, "")).strip().lower() if isinstance(raw, dict) else ""
    return value if value in _ENUMS[field] else "unknown"


def _normalize_evidence(raw) -> dict:
    result = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if str(key).strip():
                result[str(key).strip()] = str(value or "")
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                field = str(item.get("field", "")).strip()
                if field:
                    result[field] = str(item.get("snippet", "") or "")
    return result


def _normalize_facts(raw) -> dict:
    """Coerce arbitrary model output into a complete, valid facts dict."""
    if not isinstance(raw, dict):
        raw = {}
    facts = {field: _enum(raw, field) for field in _ENUMS}
    countries = raw.get("restricted_to_countries")
    facts["restricted_to_countries"] = (
        [str(c).strip().upper() for c in countries if str(c).strip()]
        if isinstance(countries, list)
        else []
    )
    facts["evidence"] = _normalize_evidence(raw.get("evidence"))
    return facts


def default_facts() -> dict:
    """A normalized facts dict with every field 'unknown' and no evidence.

    Used when a posting is decided without the LLM (e.g. the language gate
    short-circuits fact extraction), so the returned shape stays consistent.
    """
    return _normalize_facts({})


def extract_facts(client, job) -> dict:
    """Ask the model for schema-constrained posting facts; return normalized."""
    job = coerce_job(job)
    prompt = f"""You extract structured facts from a job posting for a downstream deterministic policy. Do NOT judge fit; only report what the posting states. Use "unknown" for any field the posting does not clearly state. For every non-unknown field that could decide fit, add a short VERBATIM snippet from the posting to `evidence` (field name + snippet).

## Job Posting

Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Remote: {job.get("is_remote", "")}

Description:
{section_aware_excerpt(job.get("description", ""), 5000)}

## Fields
- platform_focus: PRIMARY technology — native iOS/macOS (ios_macos); a cross-platform framework such as React Native, Flutter, Xamarin, Ionic, or Kotlin Multiplatform (cross_platform); something else (other); or unclear (unknown). Secondary/optional cross-platform mention still counts as ios_macos.
- seniority; employment_type; work_arrangement (remote/hybrid/onsite).
- remote_geo_scope: if remote, worldwide or restricted to specific countries.
- restricted_to_countries: country codes/names the remote role is restricted to.
- offers_sponsorship: relocation/visa sponsorship offered.
- authorization_blocker: states "no sponsorship", "must be authorized to work", "locals only", "W2 only", or similar.
- office_days_4plus: requires 4 or more days per week in the office.
- industry_crypto_web3: the company is a crypto/Web3 business.
- requires_us_hours: strict US-only working hours required.
"""
    raw = client.generate(prompt, temperature=0.0, response_schema=FACT_SCHEMA)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    return _normalize_facts(data)
