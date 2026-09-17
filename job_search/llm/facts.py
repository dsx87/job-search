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
    # Generic role alignment complements the legacy Apple-specific
    # ``platform_focus`` field.  It is populated against the configured search
    # terms, so a non-Apple candidate never inherits this repository's profile.
    "role_match": ("yes", "no", "unknown"),
    "office_days_per_week": ("0", "1", "2", "3", "4", "5", "6", "7", "unknown"),
    "platform_focus": ("ios_macos", "cross_platform", "other", "unknown"),
    "seniority": ("lead", "senior", "mid", "junior", "unknown"),
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
        "matched_role_terms": {"type": "array", "items": {"type": "string"}},
        "matched_required_skills": {"type": "array", "items": {"type": "string"}},
        "industries": {"type": "array", "items": {"type": "string"}},
        "description_language": {"type": "string"},
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
    for field in ("matched_role_terms", "matched_required_skills", "industries"):
        values = raw.get(field)
        facts[field] = (
            [str(value).strip() for value in values if str(value).strip()]
            if isinstance(values, list)
            else []
        )
    facts["evidence"] = _normalize_evidence(raw.get("evidence"))
    facts["description_language"] = str(raw.get("description_language", "")).strip().lower()
    return facts


def default_facts() -> dict:
    """A normalized facts dict with every field 'unknown' and no evidence.

    Used when a posting is decided without the LLM (e.g. the language gate
    short-circuits fact extraction), so the returned shape stays consistent.
    """
    return _normalize_facts({})


def _search_terms(search, name):
    return tuple(str(value).strip() for value in getattr(search, name, ()) if str(value).strip())


def build_generic_fact_context(search=None, policy=None) -> str:
    """Describe reusable extraction fields without replacing a prompt override."""
    role_terms = _search_terms(search, "role_include_terms")
    skill_groups = tuple(
        tuple(str(term).strip() for term in group if str(term).strip())
        for group in getattr(search, "skill_include_groups", ())
        if isinstance(group, (list, tuple))
    )
    industries = tuple(
        str(value).strip() for value in getattr(policy, "excluded_industries", ())
        if str(value).strip()
    )
    if not (role_terms or skill_groups or search is not None or policy is not None):
        return ""
    return """

## Reusable Candidate Context
- role_match: whether the role clearly matches one of the configured target roles (yes/no/unknown).
- matched_role_terms: configured target-role terms clearly supported by the posting.
- matched_required_skills: configured required skills clearly supported by the posting.
Configured target roles: {roles}
Configured required-skill groups (one term per group is sufficient): {skills}
- office_days_per_week: exact required office days per week (0 through 7), or unknown.
- seniority: lead, senior, mid, junior, or unknown.
- industries: named company industries/domains stated by the posting.
- description_language: language of the job description, if clear.
Configured excluded industries: {industries}
""".format(
            roles=", ".join(role_terms) or "(none)",
            skills="; ".join(" / ".join(group) for group in skill_groups) or "(none)",
            industries=", ".join(industries) or "(none)",
        )


def build_fact_extraction_prompt(job, search=None, policy=None) -> str:
    """Build the legacy fact-extraction prompt byte-for-byte without context."""
    job = coerce_job(job)
    generic_fields = build_generic_fact_context(search, policy)
    return f"""You extract structured facts from a job posting for a downstream deterministic policy. Do NOT judge fit; only report what the posting states. Use "unknown" for any field the posting does not clearly state. For every non-unknown field that could decide fit, add a short VERBATIM snippet from the posting to `evidence` (field name + snippet).

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
{generic_fields}"""


def extract_facts(client, job, prompts=None, search=None, policy=None) -> dict:
    """Ask the model for schema-constrained posting facts; return normalized."""
    if prompts is None:
        prompt = build_fact_extraction_prompt(job, search=search, policy=policy)
    else:
        prompt = prompts.fact_extraction(job)
        # File/custom prompts retain their full wording; only they need the
        # reusable context appended because the built-in builder includes it.
        generic_context = build_generic_fact_context(search, policy)
        if generic_context:
            prompt += generic_context
    raw = client.generate(prompt, temperature=0.0, response_schema=FACT_SCHEMA)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    return _normalize_facts(data)
