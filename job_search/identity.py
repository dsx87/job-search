"""Canonical, backward-compatible identities for job postings."""
from typing import Optional, Tuple

from .models import coerce_job
from .text import collapse_ws


def normalize_url(url) -> str:
    """Preserve the established lowercase, trailing-slash-free URL key."""
    return str(url or "").strip().rstrip("/").lower()


def title_company_key(title, company, location="") -> str:
    """Return the established normalized title/company/location alias."""
    key = "{}|{}".format(
        str(title or "").lower().strip(),
        str(company or "").lower().strip(),
    )
    normalized_location = collapse_ws(str(location or "").lower())
    if normalized_location:
        key = "{}|{}".format(key, normalized_location)
    return key


def identity_keys_from_fields(url="", title="", company="", location="") -> Tuple[str, ...]:
    """Return meaningful aliases without constructing a complete Job."""
    keys = []
    url_key = normalize_url(url)
    if url_key:
        keys.append(url_key)

    fallback = title_company_key(title, company, location)
    if fallback != "|" and fallback not in keys:
        keys.append(fallback)
    return tuple(keys)


def job_identity_keys(job) -> Tuple[str, ...]:
    """Return every meaningful identity alias, with URL first when present."""
    job = coerce_job(job)
    return identity_keys_from_fields(job.url, job.title, job.company, job.location)


def canonical_job_key(job) -> Optional[str]:
    """Return the preferred job identity, or None when no identity is meaningful."""
    keys = job_identity_keys(job)
    return keys[0] if keys else None
