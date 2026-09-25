"""Stable fingerprint for settings supplied to Jev."""
import hashlib
import json

_EVALUATION_SEARCH_FIELDS = (
    "role_include_terms", "role_exclude_terms", "skill_include_groups",
    "location_exclude_terms", "relocation_regions", "max_age_days",
    "remote_allowed", "relocation_allowed", "search_terms",
    "query_locations", "results_per_query",
)
_EVALUATION_CANDIDATE_FIELDS = (
    "residency_countries", "work_authorization_countries",
)
_EVALUATION_POLICY_FIELDS = (
    "check_order", "require_english", "local_language_exempt",
    "allowed_languages", "excluded_industries", "excluded_platform_focuses",
    "rejected_seniority", "max_local_office_days",
    "allow_sponsorship_override", "preferred_working_hours",
)


def _json_value(value):
    """Return a stable JSON value from the immutable settings surface."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "id"):
        return str(value.id)
    return str(value)


def evaluation_configuration_revision(search, candidate, policy):
    """Fingerprint eligibility settings without leaking CV identity/options.

    The allow-list is intentional: rendering limits, names, employer history,
    private placeholders, and any future CV-only candidate field cannot reopen
    prior job decisions.  The output is stable across process runs and does not
    depend on object identity or mutable module state.
    """
    payload = {
        "evaluator": "jev-1.13-approved-v1",
        "search": {
            field: _json_value(_option(search, field, ()))
            for field in _EVALUATION_SEARCH_FIELDS
        },
        "candidate": {
            field: _json_value(_option(candidate, field, ()))
            for field in _EVALUATION_CANDIDATE_FIELDS
        },
        "checks": {
            field: _json_value(_option(policy, field, ()))
            for field in _EVALUATION_POLICY_FIELDS
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "evaluation-config-v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _option(value, name, default):
    return getattr(value, name, default) if value is not None else default
