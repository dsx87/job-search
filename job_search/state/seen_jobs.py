"""Seen-jobs dedup state.

The on-disk format (a sorted JSON list, indent=2) is load-bearing: the daily
workflow's set-union merge across the orphan `state` branch parses it directly.
load_seen_jobs returns None (not an empty set) when the file is absent — the
first-run sentinel main uses to silence jobs older than 7 days.
"""
import json
import os

from ..config import SEEN_JOBS_FILE


def normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


def title_company_key(title: str, company: str, location: str = "") -> str:
    key = "{}|{}".format(title.lower().strip(), company.lower().strip())
    norm_location = " ".join(location.lower().split())
    if norm_location:
        key = "{}|{}".format(key, norm_location)
    return key


def load_seen_jobs():
    """Returns a set of seen URL keys, or None if the state file doesn't exist (first run)."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return None
    with open(SEEN_JOBS_FILE) as f:
        return set(json.load(f))


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)
