"""Region classification and location-token matching."""
import re

from ..models import Region
from .db import (
    AU_LOCATIONS,
    CA_LOCATIONS,
    EU_CITIES,
    EU_COUNTRIES,
    IL_LOCATIONS,
    US_LOCATIONS,
    _SHORT_LOCATION_TOKENS,
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
