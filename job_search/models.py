"""Core domain model: Region, Job, and the region maps + job serialization."""
import enum


class Region(enum.Enum):
    EU = "EU"
    CA = "CA"
    AU = "AU"
    US = "US"
    UNKNOWN = "UNKNOWN"


class Job(object):
    def __init__(
        self,
        title="",
        company="",
        location="",
        url="",
        source="",
        date_posted=None,
        description="",
        is_remote=False,
        region=Region.UNKNOWN,
        matched_skills=None,
    ):
        self.title = str(title or "").strip()
        self.company = str(company or "").strip()
        self.location = str(location or "").strip()
        self.url = str(url or "").strip()
        self.source = str(source or "").strip()
        self.date_posted = date_posted
        self.description = str(description or "")
        self.is_remote = bool(is_remote)
        self.region = region
        self.matched_skills = list(matched_skills or [])


REGION_MAP = {
    "eu": Region.EU,
    "ca": Region.CA,
    "au": Region.AU,
    "us": Region.US,
}

REGION_LABELS = {
    Region.EU: "Europe",
    Region.CA: "Canada",
    Region.AU: "Australia",
    Region.US: "United States",
    Region.UNKNOWN: "Other / Unknown",
}


def job_to_dict(job):
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "is_remote": job.is_remote,
        "region": job.region.value,
        "matched_skills": job.matched_skills,
    }
