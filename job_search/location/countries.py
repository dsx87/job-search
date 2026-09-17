"""Resolve the countries explicitly advertised in a job location string.

This module intentionally does not use ``Job.region``.  That field is a coarse
sorting hint, while consumers of this resolver need a country for every stated
location target.
"""
from dataclasses import dataclass
import re
import unicodedata

from .db import AU_LOCATIONS, CA_LOCATIONS, EU_CITIES, IL_LOCATIONS, US_LOCATIONS


# ISO 3166-1 alpha-2, including the territories assigned an official code.
ISO_COUNTRY_CODES = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI
    BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO
    CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
    FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT
    HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY
    KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP
    MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE
    PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH
    SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
    TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split()
)

EU_COUNTRY_CODES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)


@dataclass(frozen=True)
class AdvertisedLocation:
    """A country explicitly advertised for one location target."""

    country: str = ""
    region: str = ""


# Names used by job boards.  The ISO table above is deliberately complete;
# aliases are intentionally limited to common job-board spellings.
_COUNTRY_ALIASES = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "argentina": "AR",
    "armenia": "AM", "australia": "AU", "austria": "AT", "azerbaijan": "AZ",
    "bangladesh": "BD", "belarus": "BY", "belgium": "BE", "bolivia": "BO",
    "bosnia and herzegovina": "BA", "brazil": "BR", "bulgaria": "BG",
    "cambodia": "KH", "cameroon": "CM", "canada": "CA", "chile": "CL",
    "china": "CN", "colombia": "CO", "costa rica": "CR", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ", "denmark": "DK",
    "deutschland": "DE", "dominican republic": "DO", "ecuador": "EC",
    "egypt": "EG", "estonia": "EE", "ethiopia": "ET", "finland": "FI",
    "france": "FR", "georgia": "GE", "germany": "DE", "ghana": "GH",
    "greece": "GR", "guatemala": "GT", "hong kong": "HK", "hungary": "HU",
    "iceland": "IS", "india": "IN", "indonesia": "ID", "ireland": "IE",
    "israel": "IL", "italy": "IT", "japan": "JP", "kenya": "KE", "latvia": "LV",
    "lebanon": "LB", "lithuania": "LT", "luxembourg": "LU", "malaysia": "MY",
    "malta": "MT", "mexico": "MX", "moldova": "MD", "morocco": "MA",
    "netherlands": "NL", "new zealand": "NZ", "nigeria": "NG", "norway": "NO",
    "pakistan": "PK", "panama": "PA", "peru": "PE", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "puerto rico": "PR", "romania": "RO",
    "russia": "RU", "saudi arabia": "SA", "serbia": "RS", "singapore": "SG",
    "slovakia": "SK", "slovenia": "SI", "south africa": "ZA", "south korea": "KR",
    "spain": "ES", "sweden": "SE", "switzerland": "CH", "taiwan": "TW",
    "thailand": "TH", "tunisia": "TN", "turkey": "TR", "turkiye": "TR",
    "ukraine": "UA", "united arab emirates": "AE", "united kingdom": "GB",
    "united states": "US", "united states of america": "US", "usa": "US",
    "u s a": "US", "u s": "US", "uk": "GB", "great britain": "GB",
    "vietnam": "VN",
}

_EU_RE = re.compile(r"(?<![a-z0-9])(?:eu|european union)(?![a-z0-9])")
_EU_EXCLUSION_RE = re.compile(
    r"(?<![a-z0-9])(?:non[ -]+|not\s+|outside\s+(?:the\s+)?|excluding\s+)"
    r"(?:eu|european union)(?![a-z0-9])"
)
_BROAD_REGION_RE = re.compile(r"(?<![a-z0-9])(?:europe|emea|eea)(?![a-z0-9])")
_TARGET_SPLIT_RE = re.compile(r"\s*(?:/|\||;|\bor\b)\s*", re.IGNORECASE)
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT "
    "NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY".split()
)
_US_STATE_NAMES = frozenset(
    "alabama alaska arizona arkansas california colorado florida georgia illinois "
    "ohio oregon texas virginia washington".split()
)
_US_STATE_WITH_COUNTRY_RE = re.compile(
    r"^(?:alabama|alaska|arizona|arkansas|california|colorado|florida|georgia|"
    r"illinois|ohio|oregon|texas|virginia|washington)\s*,\s*"
    r"(?:united states|usa|u\.?s\.?a?)(?![a-z0-9])"
)
_CA_PROVINCE_CODES = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())
_CA_PROVINCE_NAMES = frozenset("ontario quebec alberta british columbia".split())
_HQ_PARENTHETICAL_RE = re.compile(r"\([^)]*\b(?:hq|headquarters)\b[^)]*\)", re.IGNORECASE)
_HQ_RE = re.compile(r"\b(?:company\s+)?(?:hq|headquarters)\b", re.IGNORECASE)


def _normalise(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).casefold()


_ALIAS_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(
        re.escape(name) for name in sorted(_COUNTRY_ALIASES, key=len, reverse=True)
    ) + r")(?![a-z0-9])"
)
_CITY_COUNTRIES = {
    "berlin": "DE", "munich": "DE", "hamburg": "DE", "frankfurt": "DE",
    "amsterdam": "NL", "rotterdam": "NL", "barcelona": "ES", "madrid": "ES",
    "lisbon": "PT", "porto": "PT", "stockholm": "SE", "copenhagen": "DK",
    "helsinki": "FI", "vienna": "AT", "zurich": "CH", "paris": "FR", "lyon": "FR",
    "milan": "IT", "rome": "IT", "warsaw": "PL", "prague": "CZ", "brussels": "BE",
    "oslo": "NO", "tallinn": "EE", "riga": "LV", "vilnius": "LT", "bucharest": "RO",
    "budapest": "HU", "dublin": "IE", "london": "GB", "belfast": "GB",
    "bratislava": "SK", "lima": "PE", "alofi": "NU", "tbilisi": "GE",
    "bogota": "CO", "buenos aires": "AR", "buenosaires": "AR",
    "panama city": "PA", "panamacity": "PA", "tirana": "AL",
    "st. john's": "CA", "st.john's": "CA", "st johns": "CA", "regina": "CA",
    "charlottetown": "CA", "iqaluit": "CA", "losangeles": "US", "telaviv": "IL",
    "eindhoven": "NL", "kosice": "SK", "cusco": "PE", "medellin": "CO",
    "cordoba": "AR", "podgorica": "ME", "mumbai": "IN", "batumi": "GE",
    "springfield": "US", "bozeman": "US", "savannah": "US", "valletta": "MT",
    "libreville": "GA",
}
_CITY_COUNTRIES.update({city: "IL" for city in IL_LOCATIONS})
_CITY_COUNTRIES.update({city: "CA" for city in CA_LOCATIONS - {"canada"}})
_CITY_COUNTRIES.update({city: "AU" for city in AU_LOCATIONS - {"australia"}})
_CITY_COUNTRIES.update({city: "US" for city in US_LOCATIONS - {"united states", "usa", "us", "u.s.", "u.s.a."}})

# These city/subdivision pairs have a different country from the broad city
# label above (Dublin, Ireland and London, England).  Keep that evidence
# explicit rather than treating every overlapping two-letter suffix as North
# American.
_CITY_SUBDIVISION_COUNTRIES = {
    ("dublin", "ga"): "US",
    ("london", "on"): "CA",
}
_AMBIGUOUS_SUBDIVISION_CODES = _US_STATE_CODES | _CA_PROVINCE_CODES | {"DE"}


def _target(country):
    return AdvertisedLocation(country=country, region="EU" if country in EU_COUNTRY_CODES else "")


def _named_countries(value):
    countries = []
    for match in _ALIAS_RE.finditer(value):
        country = _COUNTRY_ALIASES[match.group(0)]
        if country not in countries:
            countries.append(country)
    return countries


def _country_codes(value, raw):
    countries = []
    for match in re.finditer(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", raw):
        country = match.group(1)
        if country not in ISO_COUNTRY_CODES or country in countries:
            continue
        countries.append(country)
    return countries


def _city_country(value):
    if "northern ireland" in value or re.search(r"\bn\.?\s*ireland\b", value):
        return "GB"
    for city in sorted(_CITY_COUNTRIES, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(city) + r"(?![a-z0-9])", value):
            return _CITY_COUNTRIES[city]
    return ""


def _city_suffix_country(raw):
    """Resolve a single ``city, suffix`` target without blanket code rules.

    State and province abbreviations overlap ISO country codes.  Their meaning
    is only reliable alongside a known locality, or when the city and suffix
    name the same country.  ``DE`` is left unknown when it disagrees with the
    locality because it can mean Delaware as well as Germany.
    """
    match = re.fullmatch(r"\s*([^,]+),\s*([^,]+)\s*", str(raw or ""))
    if not match:
        return None

    city = _normalise(match.group(1)).strip()
    city_country = _city_country(city)
    raw_suffix = match.group(2).strip()
    suffix = _normalise(raw_suffix).strip(" .")
    suffix_code = raw_suffix if re.fullmatch(r"[A-Z]{2}", raw_suffix) else ""
    suffix_key = suffix_code.casefold() if suffix_code else suffix
    suffix_country = (
        suffix_code if suffix_code in ISO_COUNTRY_CODES else _COUNTRY_ALIASES.get(suffix)
    )

    if city_country and city_country == suffix_country:
        return city_country
    subdivision_country = _CITY_SUBDIVISION_COUNTRIES.get((city, suffix_key))
    if subdivision_country:
        return subdivision_country
    if city_country == "US" and (
        suffix_code in _US_STATE_CODES or suffix in _US_STATE_NAMES
    ):
        return "US"
    if city_country == "CA" and (
        suffix_code in _CA_PROVINCE_CODES or suffix in _CA_PROVINCE_NAMES
    ):
        return "CA"
    if suffix_code in _AMBIGUOUS_SUBDIVISION_CODES:
        return ""
    if suffix in _US_STATE_NAMES:
        return "" if suffix_country else "US"
    if suffix in _CA_PROVINCE_NAMES:
        return "CA"
    return suffix_country


def _resolve_target(raw):
    value = _normalise(raw).strip()
    if not value:
        return (AdvertisedLocation(),)
    if re.search(r"(?<![a-z0-9])(?:worldwide|work from anywhere|anywhere)(?![a-z0-9])", value):
        return (AdvertisedLocation(),)
    # Resolve a location pair before searching the full target for country
    # tokens: a suffix can be either an ISO country code or a subdivision code.
    suffix_country = _city_suffix_country(raw)
    if suffix_country is not None:
        return (_target(suffix_country),) if suffix_country else (AdvertisedLocation(),)
    contextual_country = _city_country(value)
    if "northern ireland" in value or re.search(r"\bn\.?\s*ireland\b", value):
        return (_target("GB"),)
    if _US_STATE_WITH_COUNTRY_RE.search(value):
        return (_target("US"),)
    countries = _named_countries(value) + _country_codes(value, str(raw or ""))
    countries = tuple(dict.fromkeys(countries))
    if countries:
        return tuple(_target(country) for country in countries)
    if contextual_country:
        return (_target(contextual_country),)
    if _EU_RE.search(value) and not _EU_EXCLUSION_RE.search(value):
        return (AdvertisedLocation(region="EU"),)
    # Broad geographical labels, unrestricted labels, and arbitrary text must
    # remain unknown so the caller can apply its own fallback policy.
    return (AdvertisedLocation(),)


def _is_country_token(value):
    normalized = _normalise(value).strip()
    raw = str(value or "").strip()
    return normalized in _COUNTRY_ALIASES or (raw.isupper() and raw in ISO_COUNTRY_CODES)


def _split_comma_country_list(target):
    parts = target.split(",")
    if _US_STATE_WITH_COUNTRY_RE.search(_normalise(target).strip()):
        return (target,)
    if len(parts) > 1 and _is_country_token(parts[0]):
        return parts
    return (target,)


def _without_hq_qualifier(target):
    """Remove employer-HQ parentheticals; omit a target that is only HQ text."""
    target = _HQ_PARENTHETICAL_RE.sub("", target).strip()
    match = _HQ_RE.search(target)
    if not match:
        return target
    prefix = target[:match.start()].rstrip(" ,;-")
    remote = re.match(r"remote\b", prefix, re.IGNORECASE)
    if remote:
        return remote.group(0)
    parts = re.split(r"\s*,\s*|\s+[—–-]\s+", prefix, maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        return parts[0].strip()
    return None


def advertised_locations(location: str) -> tuple[AdvertisedLocation, ...]:
    """Return one independently-resolved entry for each advertised target."""
    raw = str(location or "")
    targets = []
    for original in _TARGET_SPLIT_RE.split(raw):
        target = _without_hq_qualifier(original)
        if target is not None:
            targets.extend(_split_comma_country_list(target))
    resolved = tuple(item for target in targets for item in _resolve_target(target))
    return resolved or (AdvertisedLocation(),)
