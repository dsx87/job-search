"""Core domain model: Region, Job, and job serialization."""
import datetime
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from .text import collapse_ws


class Region(enum.Enum):
    EU = "EU"
    CA = "CA"
    AU = "AU"
    US = "US"
    UNKNOWN = "UNKNOWN"


@dataclass(init=False)
class Job(Mapping):
    """Canonical mutable job record with legacy read-only mapping access."""

    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    source: str = ""
    date_posted: object = None
    description: str = ""
    is_remote: bool = False
    region: Region = Region.UNKNOWN
    matched_skills: list = field(default_factory=list)

    _FIELD_NAMES: ClassVar[tuple] = (
        "title",
        "company",
        "location",
        "url",
        "source",
        "date_posted",
        "description",
        "is_remote",
        "region",
        "matched_skills",
    )

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

    def __getitem__(self, key):
        if key not in self._FIELD_NAMES:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(self._FIELD_NAMES)

    def __len__(self):
        return len(self._FIELD_NAMES)

    def to_dict(self):
        """Return the complete JSON-compatible job representation."""
        date_posted = self.date_posted
        if hasattr(date_posted, "isoformat"):
            date_posted = date_posted.isoformat()
        region = self.region.value if isinstance(self.region, Region) else str(self.region)
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "date_posted": date_posted,
            "description": self.description,
            "is_remote": self.is_remote,
            "region": region,
            "matched_skills": list(self.matched_skills),
        }

    @classmethod
    def from_dict(cls, data):
        """Build a Job from current or legacy serialized dictionaries."""
        if not isinstance(data, Mapping):
            raise TypeError("job data must be a mapping")

        date_posted = data.get("date_posted")
        if isinstance(date_posted, str) and date_posted.strip():
            value = date_posted.strip()
            try:
                date_posted = datetime.date.fromisoformat(value)
            except ValueError:
                try:
                    date_posted = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    date_posted = None

        region = data.get("region", Region.UNKNOWN)
        if not isinstance(region, Region):
            try:
                region = Region(str(region or "").strip().upper())
            except ValueError:
                region = Region.UNKNOWN

        return cls(
            title=data.get("title", ""),
            company=data.get("company", ""),
            location=data.get("location", ""),
            url=data.get("url", ""),
            source=data.get("source", ""),
            date_posted=date_posted,
            description=data.get("description", ""),
            is_remote=data.get("is_remote", False),
            region=region,
            matched_skills=data.get("matched_skills"),
        )


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


def coerce_job(value):
    """Return a canonical Job, accepting legacy mapping inputs."""
    if isinstance(value, Job):
        return value
    return Job.from_dict(value)


def job_to_dict(job):
    """Compatibility serializer for the description-free scraper CLI shape."""
    data = coerce_job(job).to_dict()
    data.pop("description")
    return data


def _richer(a, b):
    """Return whichever string carries more non-whitespace content (a wins ties)."""
    a, b = str(a or ""), str(b or "")
    return a if len(collapse_ws(a)) >= len(collapse_ws(b)) else b


def _best_date(a, b):
    """Prefer a known date; when both are known keep the more recent one."""
    if a is None:
        return b
    if b is None:
        return a
    da = a.date() if isinstance(a, datetime.datetime) else a
    db = b.date() if isinstance(b, datetime.datetime) else b
    try:
        return a if da >= db else b
    except TypeError:
        return a


def merge_jobs(base, other):
    """Combine two duplicate postings, preserving the richest available fields.

    ``base`` wins ties. Used by the filter dedup to replace first-finisher-wins
    with a merge that keeps the longest description, best URL/location, most
    recent known date, and the union of remote/region/skill signals so the AI
    evaluates the fullest version of a posting seen across sources.
    """
    base = coerce_job(base)
    other = coerce_job(other)
    skills = list(dict.fromkeys([*base.matched_skills, *other.matched_skills]))
    return Job(
        title=base.title or other.title,
        company=base.company or other.company,
        location=_richer(base.location, other.location),
        url=base.url or other.url,
        source=base.source or other.source,
        date_posted=_best_date(base.date_posted, other.date_posted),
        description=_richer(base.description, other.description),
        is_remote=bool(base.is_remote or other.is_remote),
        region=base.region if base.region != Region.UNKNOWN else other.region,
        matched_skills=skills,
    )
