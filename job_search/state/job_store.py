"""Local job store backing the curses TUI (a developer convenience tool).

State lives in job_state.json in the working directory (where the TUI is run),
never committed. This is independent of the pipeline's seen_jobs.json dedup set.
"""
import json
import os

from ..identity import canonical_job_key, job_identity_keys, normalize_url
from ..models import coerce_job

STATE_PATH = "job_state.json"


def job_to_store_dict(job):
    """Convert a scraper Job object to a storable dict."""
    data = coerce_job(job).to_dict()
    data["seen"] = False
    return data


class JobStore:
    def __init__(self, path=STATE_PATH):
        self.path = path
        self.jobs = {}
        self.show_seen = False
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.jobs = data.get("jobs", {})
                self.show_seen = data.get("show_seen", False)
            except (json.JSONDecodeError, OSError):
                self.jobs = {}
                self.show_seen = False
        else:
            self.jobs = {}
            self.show_seen = False

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"jobs": self.jobs, "show_seen": self.show_seen},
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except OSError:
            pass

    def merge(self, new_jobs):
        """Merge new jobs into the store. New jobs are unseen by default.
        Existing jobs keep their seen status. Missing jobs are removed."""
        new_keys = set()
        for value in new_jobs:
            job = coerce_job(value)
            key = canonical_job_key(job)
            if key is None:
                continue
            new_keys.add(key)
            identities = set(job_identity_keys(job))
            existing_key = next(
                (
                    stored_key
                    for stored_key, stored_job in self.jobs.items()
                    if not identities.isdisjoint(job_identity_keys(stored_job))
                ),
                None,
            )
            if existing_key is None:
                self.jobs[key] = job_to_store_dict(job)
            elif existing_key != key:
                self.jobs[key] = self.jobs.pop(existing_key)

        # Remove jobs that are no longer present
        for key in list(self.jobs.keys()):
            if key not in new_keys:
                del self.jobs[key]

        self.save()

    def toggle_seen(self, identity):
        key = identity if isinstance(identity, str) and identity in self.jobs else None
        if key is None and isinstance(identity, str):
            normalized = normalize_url(identity)
            if normalized in self.jobs:
                key = normalized
        if key is None and not isinstance(identity, str):
            key = canonical_job_key(identity)
        job = self.jobs.get(key)
        if job:
            job["seen"] = not job.get("seen", False)
            self.save()
            return job["seen"]
        return None

    def get_jobs(self, show_seen=None):
        if show_seen is None:
            show_seen = self.show_seen
        result = []
        for url, job in self.jobs.items():
            if job.get("seen", False) and not show_seen:
                continue
            result.append(job)
        # Sort by region order, then title
        region_order = {"EU": 0, "CA": 1, "AU": 2, "US": 3, "UNKNOWN": 4}
        result.sort(key=lambda j: (region_order.get(j.get("region", "UNKNOWN"), 99), j.get("title", "").lower()))
        return result

    def toggle_show_seen(self):
        self.show_seen = not self.show_seen
        self.save()
        return self.show_seen
