"""CV page-limit policy at the profile/render boundary."""

import pytest

from job_search.components import CandidateProfile
from job_search.models import Job


def test_profile_resolves_the_smallest_limit_across_advertised_targets():
    profile = CandidateProfile(
        max_pages=1,
        max_pages_by_country={"DE": 3, "FR": 2},
        max_pages_by_region={"EU": 4},
    )

    limit = profile.page_limit_for(Job(location="Remote - Germany / France"))

    assert limit.max_pages == 2
    assert limit.origin == "country:FR"


@pytest.mark.parametrize(
    "location",
    ("Remote", "Worldwide", "Europe", "EMEA", "United Kingdom", "Switzerland", "Norway"),
)
def test_profile_falls_back_when_no_advertised_target_has_a_limit(location):
    profile = CandidateProfile(
        max_pages=1,
        max_pages_by_country={"DE": 2},
        max_pages_by_region={"EU": 3},
    )

    limit = profile.page_limit_for(Job(location=location))

    assert limit.max_pages == 1
    assert limit.origin == "fallback"


def test_profile_uses_region_when_an_eu_country_has_no_country_override():
    profile = CandidateProfile(
        max_pages=1,
        max_pages_by_country={"DE": 3},
        max_pages_by_region={"EU": 2},
    )

    limit = profile.page_limit_for(Job(location="France"))

    assert limit.max_pages == 2
    assert limit.origin == "region:EU"


def test_profile_resolves_a_manually_supplied_mapping_location():
    profile = CandidateProfile(max_pages_by_country={"DE": 2})

    limit = profile.page_limit_for({"location": "Remote — Germany"})

    assert limit.max_pages == 2
    assert limit.origin == "country:DE"


@pytest.mark.parametrize("value", (True, False, 0, -1, 1.5, "2"))
def test_profile_rejects_non_integer_or_nonpositive_default_page_limits(value):
    with pytest.raises(ValueError, match="max_pages"):
        CandidateProfile(max_pages=value)


def test_profile_freezes_configured_limit_mappings():
    profile = CandidateProfile(max_pages_by_country={"DE": 2})

    with pytest.raises(TypeError):
        profile.max_pages_by_country["DE"] = 3
