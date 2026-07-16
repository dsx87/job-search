"""Canonical identity behavior shared across pipeline, state, and UI."""
import hashlib

from job_search.identity import (
    canonical_job_key,
    job_identity_keys,
    normalize_url,
    title_company_key,
)
from job_search.models import Job
from job_search.state import seen_jobs


def test_identity_keys_return_url_then_meaningful_fallback_alias():
    job = Job(
        url=" HTTPS://X.com/Role/ ",
        title=" iOS Engineer ",
        company=" Acme ",
        location=" Tel   Aviv ",
    )

    assert job_identity_keys(job) == (
        "https://x.com/role",
        "ios engineer|acme|tel aviv",
    )
    assert canonical_job_key(job) == "https://x.com/role"


def test_empty_identity_has_no_keys_or_canonical_key():
    assert job_identity_keys(Job()) == ()
    assert canonical_job_key({}) is None
    assert normalize_url("") == ""


def test_fallback_identity_supports_url_less_jobs_and_location_only_jobs():
    assert job_identity_keys(Job(title="iOS", company="Acme")) == ("ios|acme",)
    assert job_identity_keys(Job(location=" Berlin ")) == ("||berlin",)


def test_seen_jobs_reexports_identity_helpers():
    assert seen_jobs.normalize_url is normalize_url
    assert seen_jobs.title_company_key is title_company_key


def test_delivery_tokens_keep_existing_url_and_alias_hashes():
    tokens = seen_jobs.delivery_identity_tokens(
        "HTTPS://X.com/Role/", " iOS Dev ", "Acme", " Tel Aviv "
    )

    assert tokens == tuple(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in ("https://x.com/role", "ios dev|acme|tel aviv")
    )


def test_delivery_tokens_do_not_construct_full_job(monkeypatch):
    monkeypatch.setattr(
        Job,
        "from_dict",
        lambda _data: (_ for _ in ()).throw(AssertionError("no Job allocation")),
    )

    assert seen_jobs.delivery_identity_tokens(
        "https://x/role", "iOS", "Acme", "Berlin"
    )
