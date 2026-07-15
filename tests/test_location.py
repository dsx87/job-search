"""Characterization tests for region classification and location helpers."""
# --- modules under test (repoint on migration) ---
from job_search.models import Job, Region
from job_search.location.classify import (
    classify_region,
    contains_location_token,
    is_eu_member_job,
    is_israel_job,
    guess_location_from_text,
    apply_region,
)


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


def test_guess_location_from_text():
    assert guess_location_from_text("Role based in our Berlin office") == "Berlin"
    assert guess_location_from_text("We hire across the United States") == "United States"
    assert guess_location_from_text("Fully remote, anywhere") == ""


def test_apply_region_mutates_and_returns():
    j = Job(location="Toronto")
    out = apply_region(j)
    assert out is j
    assert j.region == Region.CA
