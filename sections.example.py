"""Fictional digest sections for a selected private configuration.

With no `sections.py` present the digest renders exactly as it always has: one
flat list of fits. Create the file and the dashboard groups the Fits (and,
where a section says so, the Needs-review) list under your own headings.

The list is ordered, and order is priority: each job appears exactly ONCE,
under the first section it matches. A remote Example City role lands in
"Local" below, not in "Remote — eligible", because Local comes first.
Reorder the list to re-prioritize.

The helpers keep common rules short; `match=lambda entry: ...` is equally
supported, where `entry.job` is the Job record and `entry.evaluation` is the
Jev result. Sections change presentation only: search and policy still come
from the selected TOML file.

Keep a real sections file in the private configuration checkout and point the
selected TOML's settings.sections_file at it (or use the SECTIONS_FILE override).
"""
from job_search.digest.sections import (
    Section,
    all_of,
    signal,
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
    # Remote from a candidate-eligible location.
    Section(
        "Remote — eligible",
        "🌍",
        match=all_of(is_remote, signal("location", "remote")),
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
