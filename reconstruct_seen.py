#!/usr/bin/env python3
"""One-off recovery: re-add jobs processed by run 27351267397 to seen_jobs.json.

That run processed 24 jobs and saved seen_jobs.json on the runner, but the
commit (08ec89a, "48 insertions") was rejected on push (non-fast-forward), so
the state was lost and those jobs — including 7 already sent to Telegram — would
be reprocessed/re-notified.

The run log only exposes (title, company) per job, not the URL/location needed
for the dedup keys. So we re-scrape, match each processed (title, company) pair,
and add the matched job's real url-key + title/company-key to seen_jobs.json —
exactly the keys the next run will compute, so the jobs are skipped.

Delete this file + its workflow after use.
"""

import sys

from portable_job_scraper import fetch_jobs
from pipeline import (
    SEEN_JOBS_FILE,
    load_seen_jobs,
    normalize_url,
    save_seen_jobs,
    title_company_key,
)

# Every (title, company) the failed run evaluated (24 lines; Feeld and Pinterest
# appear twice as distinct postings — duplicates collapse into the match set).
PROCESSED = [
    ("Senior Backend Monetization Engineer", "Feeld"),
    ("Expert iOS Developer", "BRD - Groupe Societe Generale"),
    ("Mobile SW Engineer (junior/medior) f/m/n", "ESET"),
    ("Junior iOS Developer", "Garmin Nederland"),
    ("Expert iOS Developer", "Societe Generale"),
    ("Senior Software Engineer, Client Applications (Europe)", "FileCloud"),
    ("IT Engineer - Device Management", "Wolt"),
    ("Software Engineer, iOS", "Pinterest"),
    ("Software Engineer - Xcode Cloud", "Apple"),
    ("Senior Mobile Engineer, Health", "Babylist"),
    ("Software Development Engineer in Test, Wireless Technologies and Ecosystems", "Apple"),
    ("Security Software Engineer, OS Security", "Apple"),
    ("Senior Azure Cloud Security Engineer", "3Bstaffing"),
    ("Data Engineer / Analyst", "McAfee"),
    ("Engineer – Mobile", "CloudWalk"),
    ("Senior Software Engineer (Rust)", "PHOTOROOM"),
    ("Senior Software Engineer | Jamf Mobile Forensics", "Jamf"),
    ("Software Engineer (Windows)", "Cato Networks"),
    ("Senior iOS Development Consultant", "intive"),
    ("Bare Developer", "Jobgether"),
    ("Senior iOS Mobile Developer - Backbase", "Jobgether"),
    ("Staff Mobile Engineer", "Jobgether"),
]


def norm(s):
    return " ".join(str(s).split()).strip().lower()


def main():
    targets = {(norm(t), norm(c)) for t, c in PROCESSED}
    print(f"Looking for {len(targets)} unique (title, company) pairs "
          f"from {len(PROCESSED)} processed jobs.", flush=True)

    raw_jobs = fetch_jobs(verbose=True)

    seen = load_seen_jobs()
    if seen is None:
        seen = set()
    before = len(seen)

    matched = set()
    added_keys = 0
    for j in raw_jobs:
        pair = (norm(j.title), norm(j.company))
        if pair not in targets:
            continue
        matched.add(pair)
        for k in (normalize_url(j.url), title_company_key(j.title, j.company, j.location)):
            if k and k not in seen:
                seen.add(k)
                added_keys += 1

    save_seen_jobs(seen)

    print(f"\nMatched {len(matched)}/{len(targets)} target pairs.", flush=True)
    print(f"seen_jobs.json: {before} -> {len(seen)} keys (+{added_keys}).", flush=True)

    missing = sorted(targets - matched)
    if missing:
        print("\nNOT matched (likely expired/changed since the run; "
              "may be reprocessed next run):", flush=True)
        for t, c in missing:
            print(f"  - {t!r} at {c!r}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
