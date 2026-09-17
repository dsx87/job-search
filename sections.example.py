"""Fictional digest sections — copy to `sections.py` to switch grouping on.

With no `sections.py` present the digest renders exactly as it always has: one
flat list of fits. Create the file and the dashboard groups the Fits (and,
where a section says so, the Needs-review) list under your own headings.

The list is ordered, and order is priority: each job appears exactly ONCE,
under the first section it matches. A remote Example City role lands in
"Local" below, not in "Remote — Worldwide", because Local comes first.
Reorder the list to re-prioritize.

The helpers keep common rules short; `match=lambda entry: ...` is equally
supported, where `entry.job` is the Job record and `entry.evaluation` is the
LLM result. Sections change presentation only: search and policy still come
from the selected TOML file.

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
from job_search.models import Region

SECTIONS = [
    # Fictional local roles come first because section order is priority.
    Section(
        "Local",
        "🏠",
        applies_to=("fits", "review"),
        match=on_job(lambda job: "example city" in job.location.lower()),
    ),
    # Remote with no geographic restriction — the most applicable bucket.
    Section(
        "Remote — Worldwide",
        "🌍",
        match=all_of(is_remote, fact("remote_geo_scope", "worldwide")),
    ),
    # On-site or hybrid in the EU: relevant when relocation is configured.
    Section(
        "EU relocation",
        "✈️",
        match=all_of(in_region(Region.EU), not_(is_remote)),
    ),
    # No `match` means "everything left". Without a catch-all like this the
    # remainder still appears, under an automatic "Other" heading.
    Section("Everything else", "📋"),
]
