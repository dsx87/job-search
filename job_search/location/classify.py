"""Region classification and location-token matching."""
import re

from ..models import Region
from .db import (
    AU_LOCATIONS,
    CA_LOCATIONS,
    EU_CITIES,
    EU_COUNTRIES,
    EU_MEMBER_CITIES,
    EU_MEMBER_COUNTRIES,
    IL_LOCATIONS,
    US_LOCATIONS,
    _SHORT_LOCATION_TOKENS,
)


_NORTHERN_IRELAND_RE = re.compile(
    r"(?<![a-z0-9])northern[\s-]+ireland(?![a-z0-9])"
)
_NEGATED_EU_RE = re.compile(r"(?<![a-z0-9])non[\s-]+eu(?![a-z0-9])")
_POSITIVE_EU_TOKENS = {"eu", "european union"}
_EU_MEMBER_COUNTRY_NAMES = EU_MEMBER_COUNTRIES - _POSITIVE_EU_TOKENS
_NON_EU_LOCATION_TOKENS = (
    CA_LOCATIONS
    | AU_LOCATIONS
    | US_LOCATIONS
    | (EU_COUNTRIES - EU_MEMBER_COUNTRIES)
)


def contains_location_token(location, token):
    if token in _SHORT_LOCATION_TOKENS:
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", location)
        )
    return token in location


def classify_region(job):
    loc = job.location.lower()
    for token in EU_COUNTRIES | EU_CITIES:
        if contains_location_token(loc, token):
            return Region.EU
    for token in CA_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.CA
    for token in AU_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.AU
    for token in US_LOCATIONS:
        if contains_location_token(loc, token):
            return Region.US
    return Region.UNKNOWN


def is_eu_member_job(job):
    """Return whether the location explicitly identifies the EU or an EU-27 member."""
    raw_loc = job.location.lower()
    has_northern_ireland = bool(_NORTHERN_IRELAND_RE.search(raw_loc))
    has_negated_eu = bool(_NEGATED_EU_RE.search(raw_loc))
    loc = _NEGATED_EU_RE.sub(" ", _NORTHERN_IRELAND_RE.sub(" ", raw_loc))

    if any(
        contains_location_token(loc, token)
        for token in _EU_MEMBER_COUNTRY_NAMES | _POSITIVE_EU_TOKENS
    ):
        return True

    has_member_city = any(
        contains_location_token(loc, token) for token in EU_MEMBER_CITIES
    )
    has_conflicting_location = has_northern_ireland or any(
        contains_location_token(loc, token) for token in _NON_EU_LOCATION_TOKENS
    )
    return has_member_city and not has_negated_eu and not has_conflicting_location


def apply_region(job):
    job.region = classify_region(job)
    return job


def is_israel_job(job):
    loc = job.location.lower()
    for token in IL_LOCATIONS:
        if contains_location_token(loc, token):
            return True
    return False


def guess_location_from_text(text):
    lower = str(text or "").lower()
    token_sets = [
        (Region.EU, EU_COUNTRIES | EU_CITIES),
        (Region.CA, CA_LOCATIONS),
        (Region.AU, AU_LOCATIONS),
        (Region.US, US_LOCATIONS),
    ]
    for _region, tokens in token_sets:
        for token in sorted(tokens, key=len, reverse=True):
            if contains_location_token(lower, token):
                return token.title()
    return ""
