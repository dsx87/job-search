"""Structured CV-edit extraction: which base bullets to keep per job.

The model returns a small selection object (bullet indices per company) instead
of full LaTeX — far fewer output tokens, no fabrication, and no compile-repair
loop, since a deterministic renderer builds the CV from the trusted base (audit
Finding 19).
"""
import json

from ..latex.tailor_render import extract_job_bullets
from ..models import coerce_job
from ..profile import EXPECTED_JOB_ORDER
from ..text import section_aware_excerpt

CV_EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "keep_bullets": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
    },
}


def _canonical_company(name):
    for key in EXPECTED_JOB_ORDER:
        if key.lower() in str(name or "").lower():
            return key
    return None


def _one_line(text):
    return " ".join(str(text).split())


def _parse_selection(raw) -> dict:
    """Coerce the model's JSON into {company: [int indices]}; {} on garbage."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return {}
    selection = {}
    for entry in jobs:
        if not isinstance(entry, dict):
            continue
        company = _canonical_company(entry.get("company"))
        if company is None:
            continue
        keep = entry.get("keep_bullets")
        indices = (
            [i for i in keep if isinstance(i, int) and not isinstance(i, bool)]
            if isinstance(keep, list)
            else []
        )
        selection[company] = indices
    return selection


def build_cv_bullet_selection_prompt(base_tex, job) -> str:
    """Build the legacy deterministic-bullet selection prompt byte-for-byte."""
    job = coerce_job(job)
    jobs = extract_job_bullets(base_tex)
    lines = [
        "You tailor Igor Pivnyk's CV by SELECTING which existing résumé bullets to keep "
        "for each job, most relevant first. Return only bullet indices — never rewrite text. "
        "Keep the strongest, most posting-relevant bullets and drop the least relevant; keep "
        "at least 2 bullets per job.",
        "",
        "## Résumé bullets (per company)",
    ]
    for entry in jobs:
        lines.append(f"\n{entry['company']}:")
        for index, bullet in enumerate(entry["bullets"]):
            lines.append(f"  [{index}] {_one_line(bullet)}")
    lines.extend([
        "",
        "## Job Posting",
        "",
        f"Title: {job.get('title', '')}",
        f"Company: {job.get('company', '')}",
        f"Location: {job.get('location', '')}",
        "",
        section_aware_excerpt(job.get("description", ""), 7000),
        "",
        'Return JSON {"jobs": [{"company": <name>, "keep_bullets": [<indices in priority '
        "order>]}]} covering every company above.",
    ])
    return "\n".join(lines)


def select_cv_bullets(client, base_tex, job, prompts=None) -> dict:
    """Ask the model which base bullets to keep per job; return {company: [idx]}."""
    prompt = (
        prompts.cv_bullet_selection(base_tex, job) if prompts is not None
        else build_cv_bullet_selection_prompt(base_tex, job)
    )
    raw = client.generate(prompt, temperature=0.0, response_schema=CV_EDIT_SCHEMA)
    return _parse_selection(raw)
