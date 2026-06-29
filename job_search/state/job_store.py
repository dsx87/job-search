"""Local job store backing the curses TUI (a developer convenience tool).

State lives in job_state.json in the working directory (where the TUI is run),
never committed. This is independent of the pipeline's seen_jobs.json dedup set.
"""
import json
import os

STATE_PATH = "job_state.json"


def job_to_store_dict(job):
    """Convert a scraper Job object to a storable dict."""
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "is_remote": job.is_remote,
        "region": job.region.value if job.region else "UNKNOWN",
        "matched_skills": job.matched_skills,
        "seen": False,
    }


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
        new_urls = set()
        for job in new_jobs:
            url = job.url
            if not url:
                continue
            new_urls.add(url)
            if url not in self.jobs:
                self.jobs[url] = job_to_store_dict(job)

        # Remove jobs that are no longer present
        for url in list(self.jobs.keys()):
            if url not in new_urls:
                del self.jobs[url]

        self.save()

    def toggle_seen(self, url):
        job = self.jobs.get(url)
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
