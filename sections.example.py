"""Example digest sections — copy to `sections.py` to switch grouping on.

With no `sections.py` present the digest renders exactly as it always has: one
flat list of fits. Create the file and the dashboard groups the Fits (and,
where a section says so, the Needs-review) list under your own headings.

The list is ordered, and order is priority: each job appears exactly ONCE,
under the first section it matches. A remote Tel Aviv role lands in "Israel"
below, not in "Remote — Worldwide", because Israel comes first. Reorder the
list to re-prioritize.

Anything in this repo is importable here, which is the point of the file being
Python — `on_job(is_israel_job)` reuses the real location database instead of
re-listing city names. The helpers are only there to keep the common case
short; `match=lambda entry: ...` is equally supported, where `entry.job` is the
Job record and `entry.evaluation` is the LLM result.

Point the pipeline at a different file with the SECTIONS_FILE env var.
"""
from job_search.digest.sections import (
    Section,
    all_of,
    fact,
    in_region,
    is_remote,
    not_,
    on_job,
)
from job_search.location.classify import is_israel_job
from job_search.models import Region

SECTIONS = [
    # Israel roles are judged on office-days by criteria.md rather than the
    # remote filter, so they are worth pulling out first.
    Section(
        "Israel",
        "🇮🇱",
        applies_to=("fits", "review"),
        match=on_job(is_israel_job),
    ),
    # Remote with no geographic restriction — the most applicable bucket.
    Section(
        "Remote — Worldwide",
        "🌍",
        match=all_of(is_remote, fact("remote_geo_scope", "worldwide")),
    ),
    # On-site or hybrid in the EU: relevant only with relocation on the table.
    Section(
        "EU relocation",
        "✈️",
        match=all_of(in_region(Region.EU), not_(is_remote)),
    ),
    # No `match` means "everything left". Without a catch-all like this the
    # remainder still appears, under an automatic "Other" heading.
    Section("Everything else", "📋"),
]
