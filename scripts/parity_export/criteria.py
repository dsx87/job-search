"""Criteria area: Jev's approved checks, the findings() colours, and settings keys.

Checks and choices are lists in engine order, because canonical JSON sorts
object keys and the order is part of what a template copies.
"""
from job_search.config import CandidateConfig, PolicyConfig, SearchConfig
from job_search.evaluation_config import (
    _EVALUATION_CANDIDATE_FIELDS,
    _EVALUATION_POLICY_FIELDS,
    _EVALUATION_SEARCH_FIELDS,
)
from job_search.jev import APPROVED_CHECKS, findings, jev_payload
from job_search.models import Job
from job_search.settings import _CHECK_IDS

_OUTCOME = {"green": "pass", "red": "reject", "unclear": "unclear"}

NOTES = [
    "outcome is the Decision 23 mapping of the colour findings() gives a choice: green -> pass, "
    "red -> reject, unclear -> unclear.",
    "findings() gives no row for unknown_red_flag 'no' (findings_row is null); its outcome is pass.",
    "findings() colours unknown_red_flag 'yes' red, not unclear. evaluate_job leaves that label "
    "out of its named-red test, so a red flag alone turns nonfit into review, never into a "
    "rejection; Decision 23 maps it to unclear, and outcome here follows Decision 23.",
    "The policy key -> check mapping of Decision 23 is not in the engine; only the field lists "
    "are exported.",
]


def _choice(check, choice, detail):
    rows = findings({"signals": {check: {"choice": choice}}})
    if len(rows) > 1:
        raise RuntimeError(f"findings() gave {len(rows)} rows for {check}/{choice}")
    row = rows[0] if rows else None
    if row is None:
        if (check, choice) != ("unknown_red_flag", "no"):
            raise RuntimeError(f"findings() gave no row for {check}/{choice}")
        outcome = "pass"
    elif check == "unknown_red_flag" and choice == "yes":
        outcome = "unclear"
    else:
        outcome = _OUTCOME[row["color"]]
    return {
        "name": choice,
        "question": detail["question"],
        "description": detail["description"],
        "color": detail["color"],
        "findings_row": row,
        "outcome": outcome,
    }


def _checks():
    return [
        {
            "jev_name": check,
            "label": spec["label"],
            "choices": [_choice(check, choice, detail) for choice, detail in spec["choices"].items()],
        }
        for check, spec in APPROVED_CHECKS.items()
    ]


def _settings():
    state = jev_payload(Job(), "", CandidateConfig(), PolicyConfig(), SearchConfig())["state"]
    return {
        "evaluation_search_fields": list(_EVALUATION_SEARCH_FIELDS),
        "evaluation_candidate_fields": list(_EVALUATION_CANDIDATE_FIELDS),
        "evaluation_policy_fields": list(_EVALUATION_POLICY_FIELDS),
        "jev_payload_search_settings": list(state["search_settings"]),
        "jev_payload_policy_settings": list(state["policy_settings"]),
        "jev_payload_candidate_settings": list(state["candidate_settings"]),
        "default_check_order": list(PolicyConfig().check_order),
        "check_ids": sorted(_CHECK_IDS),
    }


def export():
    return {"checks.json": _checks(), "settings.json": _settings()}
