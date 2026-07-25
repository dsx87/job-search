"""Characterization tests for region classification and location helpers."""
import pytest

# --- modules under test (repoint on migration) ---
from job_search.models import Job, Region
from job_search.location.classify import (
    classify_region,
    contains_location_token,
    is_eu_member_job,
    is_israel_job,
    guess_location_from_text,
    apply_region,
    remote_residency_restriction,
)
from job_search.filters.rules import opportunity_filter


def test_remote_residency_restriction_flags_specific_geographies():
    assert remote_residency_restriction(Job(location="Remote - United Kingdom")) == "restricted"
    assert remote_residency_restriction(Job(location="USA")) == "restricted"
    assert remote_residency_restriction(Job(location="Europe")) == "restricted"
    assert remote_residency_restriction(Job(location="United States, Canada")) == "restricted"


def test_remote_residency_restriction_allows_worldwide_and_israel():
    assert remote_residency_restriction(Job(location="Remote")) == "unrestricted"
    assert remote_residency_restriction(Job(location="")) == "unrestricted"
    assert remote_residency_restriction(Job(location="Worldwide")) == "unrestricted"
    assert remote_residency_restriction(Job(location="Anywhere")) == "unrestricted"
    assert remote_residency_restriction(Job(location="Remote - Israel")) == "unrestricted"
    assert remote_residency_restriction(Job(location="Remote - EMEA")) == "unrestricted"


def test_is_eu_member_job_is_strict():
    assert is_eu_member_job(Job(location="Berlin, Germany")) is True
    assert is_eu_member_job(Job(location="Paris, France")) is True
    assert is_eu_member_job(Job(location="Remote - EU")) is True
    assert is_eu_member_job(Job(location="European Union")) is True
    assert is_eu_member_job(Job(location="Dublin, Ireland")) is True
    assert is_eu_member_job(Job(location="Belfast, Northern Ireland")) is False
    assert is_eu_member_job(
        Job(location="Dublin, Ireland / Belfast, Northern Ireland")
    ) is True

    assert is_eu_member_job(Job(location="London, United Kingdom")) is False
    assert is_eu_member_job(Job(location="Zurich, Switzerland")) is False
    assert is_eu_member_job(Job(location="Oslo, Norway")) is False
    assert is_eu_member_job(Job(location="Europe")) is False
    assert is_eu_member_job(Job(location="EMEA")) is False


@pytest.mark.parametrize(
    "location",
    [
        "Belfast, Northern-Ireland",
        "Belfast, Northern  Ireland",
        "Belfast, N. Ireland",
        "Belfast, N Ireland",
        "Belfast, N.Ireland",
    ],
)
def test_is_eu_member_job_rejects_northern_ireland_spelling_variants(location):
    assert is_eu_member_job(Job(location=location)) is False


@pytest.mark.parametrize(
    "location",
    [
        "Remote - non-EU",
        "Dublin, Ohio, USA",
        "Athens, Georgia, USA",
        "Dublin, Canada",
        "Athens, Australia",
        "Dublin, United Kingdom",
        # US state postal codes without a spelled-out country
        "Dublin, GA",
        "Athens, OH",
        "Dublin, OH",
    ],
)
def test_is_eu_member_job_rejects_negation_and_conflicting_city_evidence(location):
    assert is_eu_member_job(Job(location=location)) is False


@pytest.mark.parametrize(
    "location",
    [
        # A genuine member city plus a broad-Europe umbrella still qualifies.
        "Munich, EMEA",
        "Berlin, Europe",
        "Amsterdam, EEA",
        # DE is the Germany country code, not the Delaware state code.
        "Munich, DE",
        # Irish "Co." county notation must not read as the Colorado state code.
        "Dublin, Co. Dublin",
    ],
)
def test_is_eu_member_job_keeps_member_city_with_broad_or_ambiguous_qualifier(location):
    assert is_eu_member_job(Job(location=location)) is True


@pytest.mark.parametrize(
    "location",
    [
        "Dublin, Ireland / Belfast, Northern-Ireland",
        "Berlin, Germany / USA",
        "Remote - EU / USA",
        "Non-EU applicants / Paris, France",
    ],
)
def test_is_eu_member_job_keeps_independent_positive_eu_evidence(location):
    assert is_eu_member_job(Job(location=location)) is True


def test_classify_region():
    assert classify_region(Job(location="Berlin, Germany")) == Region.EU
    assert classify_region(Job(location="Toronto, Canada")) == Region.CA
    assert classify_region(Job(location="Sydney")) == Region.AU
    assert classify_region(Job(location="New York, USA")) == Region.US
    assert classify_region(Job(location="Remote")) == Region.UNKNOWN
    # Israel is not an EU/CA/AU/US region — classified UNKNOWN here.
    assert classify_region(Job(location="Tel Aviv, Israel")) == Region.UNKNOWN


def test_contains_location_token_short_tokens_need_boundaries():
    assert contains_location_token("us only role", "us") is True
    assert contains_location_token("austin texas", "us") is False  # inside a word
    assert contains_location_token("germany", "eu") is False
    assert contains_location_token("europe wide", "europe") is True  # long token = substring


def test_is_israel_job():
    assert is_israel_job(Job(location="Tel Aviv")) is True
    assert is_israel_job(Job(location="Tel-Aviv, Israel")) is True
    assert is_israel_job(Job(location="Haifa")) is True
    assert is_israel_job(Job(location="Berlin, Germany")) is False


def test_is_israel_job_recognizes_the_remaining_tech_hubs():
    # audit finding 10: bare "Ramat Gan" (the Diamond Exchange tech cluster) went
    # unrecognized, so opportunity_filter never applied the Israel exception.
    for location in (
        "Ramat Gan",
        "Ramat-Gan, Israel",
        "Givatayim",
        "Herzliya Pituach",
        "Ramat HaSharon",
        "Hod HaSharon",
        "Kiryat Ono",
        "Nes Ziona",
        "Yavne",
        "Karmiel",
        "Migdal HaEmek",
    ):
        assert is_israel_job(Job(location=location)) is True, location


def test_ramat_gan_job_survives_the_opportunity_filter():
    # The point of recognizing it: an office-based Israeli role must reach the
    # LLM rather than be dropped for having no remote/relocation evidence.
    job = Job(
        title="iOS Developer",
        location="Ramat Gan",
        description="Senior iOS engineer for our Bursa district office.",
    )
    assert opportunity_filter(job) is True


def test_guess_location_from_text():
    assert guess_location_from_text("Role based in our Berlin office") == "Berlin"
    assert guess_location_from_text("We hire across the United States") == "United States"
    assert guess_location_from_text("Fully remote, anywhere") == ""


def test_apply_region_mutates_and_returns():
    j = Job(location="Toronto")
    out = apply_region(j)
    assert out is j
    assert j.region == Region.CA
