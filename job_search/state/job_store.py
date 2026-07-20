"""Local job store backing the curses TUI (a developer convenience tool).

State lives in job_state.json in the working directory (where the TUI is run),
never committed. This is independent of the pipeline's seen_jobs.json dedup set.
"""
import json
import os

from ..identity import canonical_job_key, normalize_url, title_company_key
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

    def merge(self, new_jobs, incomplete_sources=()):
        """Merge new jobs into the store. New jobs are unseen by default.
        Existing jobs keep their seen status. Missing jobs are removed."""
        url_index, fallback_index, url_less_fallback_index = self._identity_indexes()
        merged = {}
        for value in new_jobs:
            job = coerce_job(value)
            key = canonical_job_key(job)
            if key is None:
                continue
            url_key = normalize_url(job.url)
            fallback_key = title_company_key(job.title, job.company, job.location)
            existing_key = url_index.get(url_key) if url_key else None
            if existing_key is None and fallback_key != "|":
                if url_key:
                    # A fallback alias can migrate a URL-less legacy record, but
                    # two records with different URLs are distinct postings.
                    existing_key = url_less_fallback_index.get(fallback_key)
                else:
                    existing_key = fallback_index.get(fallback_key)

            previous = merged.get(key)
            if previous is None and existing_key is not None:
                previous = self.jobs.get(existing_key)
            data = job_to_store_dict(job)
            if previous is not None:
                data["seen"] = previous.get("seen", False)
            merged[key] = data

        incomplete_sources = set(incomplete_sources or ())
        for key, value in self.jobs.items():
            if key not in merged and value.get("source") in incomplete_sources:
                merged[key] = value

        self.jobs = merged

        self.save()

    def toggle_seen(self, identity):
        key = identity if isinstance(identity, str) and identity in self.jobs else None
        if key is None:
            for stored_key, stored_job in self.jobs.items():
                if stored_job is identity:
                    key = stored_key
                    break
        if key is None:
            url_index, fallback_index, _url_less = self._identity_indexes()
            if isinstance(identity, str):
                key = url_index.get(normalize_url(identity))
            else:
                job = coerce_job(identity)
                url_key = normalize_url(job.url)
                key = url_index.get(url_key) if url_key else None
                if key is None:
                    fallback = title_company_key(job.title, job.company, job.location)
                    key = fallback_index.get(fallback)
        job = self.jobs.get(key)
        if job:
            job["seen"] = not job.get("seen", False)
            self.save()
            return job["seen"]
        return None

    def _identity_indexes(self):
        """Build constant-time lookups for the current stored snapshot."""
        url_index = {}
        fallback_index = {}
        url_less_fallback_index = {}
        for stored_key, value in self.jobs.items():
            try:
                job = coerce_job(value)
            except TypeError:
                continue
            url_key = normalize_url(job.url)
            fallback_key = title_company_key(job.title, job.company, job.location)
            if url_key:
                url_index.setdefault(url_key, stored_key)
            if fallback_key != "|":
                fallback_index.setdefault(fallback_key, stored_key)
                if not url_key:
                    url_less_fallback_index.setdefault(fallback_key, stored_key)
        return url_index, fallback_index, url_less_fallback_index

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
