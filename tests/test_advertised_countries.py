"""Behavioral tests for advertised job-location country resolution."""
import dataclasses

import pytest

from job_search.location.countries import (
    AdvertisedLocation,
    EU_COUNTRY_CODES,
    ISO_COUNTRY_CODES,
    advertised_locations,
)


def _targets(location):
    return tuple((target.country, target.region) for target in advertised_locations(location))


def test_public_country_sets_are_immutable_and_cover_expected_membership():
    assert isinstance(ISO_COUNTRY_CODES, frozenset)
    assert isinstance(EU_COUNTRY_CODES, frozenset)
    assert len(ISO_COUNTRY_CODES) == 249
    assert {"DE", "GB", "IL", "US"} <= ISO_COUNTRY_CODES
    assert len(EU_COUNTRY_CODES) == 27
    assert {"DE", "IE", "FR"} <= EU_COUNTRY_CODES
    assert {"GB", "CH", "NO"}.isdisjoint(EU_COUNTRY_CODES)


def test_advertised_location_is_an_immutable_blank_default_value():
    target = AdvertisedLocation()
    assert target == AdvertisedLocation(country="", region="")
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.country = "DE"


@pytest.mark.parametrize("location", ["", "Remote", "Worldwide", "Work from anywhere", "Unspecified"])
def test_unrestricted_or_unrecognized_locations_remain_unknown(location):
    assert _targets(location) == (("", ""),)


@pytest.mark.parametrize("location", ["EU", "European Union", "Remote - EU"])
def test_explicit_eu_without_a_country_keeps_the_eu_region(location):
    assert _targets(location) == (("", "EU"),)


@pytest.mark.parametrize(
    "location",
    [
        "Remote outside EU",
        "Remote - not EU",
        "Europe excluding EU",
        "Remote - non EU",
        "Remote - non-European Union",
        "Remote outside the European Union",
    ],
)
def test_negated_or_excluded_eu_does_not_create_a_positive_eu_target(location):
    assert _targets(location) == (("", ""),)


@pytest.mark.parametrize("location", ["Europe", "EMEA", "EEA", "Remote across Europe"])
def test_broad_geography_is_not_treated_as_eu(location):
    assert _targets(location) == (("", ""),)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Berlin, Germany", (("DE", "EU"),)),
        ("Berlin, Deutschland", (("DE", "EU"),)),
        ("Dublin, Ireland", (("IE", "EU"),)),
        ("London, United Kingdom", (("GB", ""),)),
        ("Toronto, Canada", (("CA", ""),)),
        ("Tel Aviv, Israel", (("IL", ""),)),
        ("United States", (("US", ""),)),
        ("Georgia", (("GE", ""),)),
        ("Georgia, USA", (("US", ""),)),
        ("DE", (("DE", "EU"),)),
        ("Remote - DE", (("DE", "EU"),)),
    ],
)
def test_country_names_aliases_and_city_context_resolve_country(location, expected):
    assert _targets(location) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Dublin, GA", (("US", ""),)),
        ("London, Ontario", (("CA", ""),)),
        ("London, ON", (("CA", ""),)),
        ("St. John's, NL", (("CA", ""),)),
        ("Regina, SK", (("CA", ""),)),
        ("Charlottetown, PE", (("CA", ""),)),
        ("Iqaluit, NU", (("CA", ""),)),
        ("Belfast, Northern Ireland", (("GB", ""),)),
        ("Dublin, Co. Dublin", (("IE", "EU"),)),
        ("Dublin, DE", (("", ""),)),
        ("Berlin, DE", (("DE", "EU"),)),
    ],
)
def test_city_and_state_ambiguities_use_explicit_context(location, expected):
    assert _targets(location) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Amsterdam, NL", (("NL", "EU"),)),
        ("St. John's, NL", (("CA", ""),)),
        ("Bratislava, SK", (("SK", "EU"),)),
        ("Regina, SK", (("CA", ""),)),
        ("Lima, PE", (("PE", ""),)),
        ("Charlottetown, PE", (("CA", ""),)),
        ("Alofi, NU", (("NU", ""),)),
        ("Iqaluit, NU", (("CA", ""),)),
        ("Toronto, CA", (("CA", ""),)),
        ("Vancouver, CA", (("CA", ""),)),
        ("Los Angeles, CA", (("US", ""),)),
        ("Tel Aviv, IL", (("IL", ""),)),
        ("Chicago, IL", (("US", ""),)),
        ("Tbilisi, Georgia", (("GE", ""),)),
        ("Atlanta, Georgia", (("US", ""),)),
        ("Bogota, CO", (("CO", ""),)),
        ("Buenos Aires, AR", (("AR", ""),)),
        ("Panama City, PA", (("PA", ""),)),
        ("Tirana, AL", (("AL", ""),)),
        ("Berlin, DE", (("DE", "EU"),)),
        ("Dublin, DE", (("", ""),)),
    ],
)
def test_city_and_suffix_context_resolves_overlapping_country_and_subdivision_codes(location, expected):
    assert _targets(location) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Eindhoven, NL", (("NL", "EU"),)),
        ("Kosice, SK", (("SK", "EU"),)),
        ("Cusco, PE", (("PE", ""),)),
        ("Medellin, CO", (("CO", ""),)),
        ("Cordoba, AR", (("AR", ""),)),
        ("Podgorica, ME", (("ME", ""),)),
        ("Mumbai, IN", (("IN", ""),)),
        ("Batumi, Georgia", (("GE", ""),)),
    ],
)
def test_unlisted_city_with_an_iso_suffix_does_not_inherit_subdivision_meaning(location, expected):
    assert _targets(location) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Springfield, IL", (("US", ""),)),
        ("Tel Aviv, IL", (("IL", ""),)),
        ("Bozeman, MT", (("US", ""),)),
        ("Valletta, MT", (("MT", "EU"),)),
        ("Savannah, GA", (("US", ""),)),
        ("Libreville, GA", (("GA", ""),)),
    ],
)
def test_recognized_city_disambiguates_an_overlapping_state_and_iso_suffix(location, expected):
    assert _targets(location) == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Imaginary City, IL", (("", ""),)),
        ("Imaginary City, MT", (("", ""),)),
        ("Imaginary City, GA", (("", ""),)),
        ("Imaginary City, NL", (("", ""),)),
        ("Imaginary City, FR", (("FR", "EU"),)),
    ],
)
def test_unknown_city_with_an_ambiguous_suffix_remains_unknown(location, expected):
    assert _targets(location) == expected


def test_multiple_advertised_countries_are_preserved_independently():
    assert _targets("Germany / Israel") == (("DE", "EU"), ("IL", ""))
    assert _targets("Germany, Israel") == (("DE", "EU"), ("IL", ""))
    assert _targets("DE, FR") == (("DE", "EU"), ("FR", "EU"))


def test_multiple_targets_keep_unknown_parts_for_consumer_fallback():
    assert _targets("Germany / EMEA") == (("DE", "EU"), ("", ""))
    assert _targets("Germany / Worldwide") == (("DE", "EU"), ("", ""))
    assert _targets("Germany or unknown") == (("DE", "EU"), ("", ""))
    assert _targets("Germany, Atlantis") == (("DE", "EU"), ("", ""))
    assert _targets("Germany outside EU working hours") == (("DE", "EU"),)


def test_unrestricted_advertisement_does_not_infer_a_country_from_hq_context():
    assert _targets("Worldwide (Germany headquarters)") == (("", ""),)
    assert _targets("Remote (Germany HQ)") == (("", ""),)
    assert _targets("Remote; company headquarters in Germany") == (("", ""),)
    assert _targets("Remote, Germany headquarters") == (("", ""),)
    assert _targets("Remote — our Berlin HQ") == (("", ""),)
    assert _targets("Remote — based from our Berlin headquarters") == (("", ""),)
    assert _targets("Paris (company headquarters Berlin)") == (("FR", "EU"),)
