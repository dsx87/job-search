"""CV page-limit policy at the profile/render boundary."""

import pytest
from pathlib import Path

from job_search.components import CandidateProfile
from job_search.models import Job


FIXTURE = Path(__file__).parent / "fixtures" / "fictional_candidate.tex"


def _profile(**overrides):
    values = {
        "display_name": "Avery Example",
        "base_tex_path": str(FIXTURE),
        "cv_filename_prefix": "avery_example_cv",
        "employer_order": ("Example Labs", "Sample Systems"),
        "forbidden_claim_patterns": (),
    }
    values.update(overrides)
    return CandidateProfile(**values)


def test_profile_resolves_the_smallest_limit_across_advertised_targets():
    profile = _profile(
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
    profile = _profile(
        max_pages=1,
        max_pages_by_country={"DE": 2},
        max_pages_by_region={"EU": 3},
    )

    limit = profile.page_limit_for(Job(location=location))

    assert limit.max_pages == 1
    assert limit.origin == "fallback"


def test_profile_uses_region_when_an_eu_country_has_no_country_override():
    profile = _profile(
        max_pages=1,
        max_pages_by_country={"DE": 3},
        max_pages_by_region={"EU": 2},
    )

    limit = profile.page_limit_for(Job(location="France"))

    assert limit.max_pages == 2
    assert limit.origin == "region:EU"


def test_profile_resolves_a_manually_supplied_mapping_location():
    profile = _profile(max_pages_by_country={"DE": 2})

    limit = profile.page_limit_for({"location": "Remote — Germany"})

    assert limit.max_pages == 2
    assert limit.origin == "country:DE"


@pytest.mark.parametrize("value", (True, False, 0, -1, 1.5, "2"))
def test_profile_rejects_non_integer_or_nonpositive_default_page_limits(value):
    with pytest.raises(ValueError, match="max_pages"):
        _profile(max_pages=value)


def test_profile_freezes_configured_limit_mappings():
    profile = _profile(max_pages_by_country={"DE": 2})

    with pytest.raises(TypeError):
        profile.max_pages_by_country["DE"] = 3
