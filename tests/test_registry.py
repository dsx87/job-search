"""Characterization test for the source registry order (load-bearing).

The fetch dispatch order and the --list-sources output both depend on this
exact 19-name insertion sequence.
"""
# --- module under test (repoint on migration) ---
from job_search import sources as scraper

EXPECTED_ORDER = [
    "jobspy",
    "linkedin-global",
    "linkedin-israel",
    "arc",
    "mobile.career",
    "jobscroller",
    "arbeitnow",
    "jobicy",
    "remoteok",
    "weworkremotely",
    "remotefirstjobs",
    "remotevibe",
    "himalayas",
    "remotive",
    "workingnomads",
    "themuse",
    "swissdevjobs",
    "relocate.me",
    "secrettelaviv",
]

OPTIONAL_DEPENDENCIES = {
    "jobspy": "python-jobspy",
    "linkedin-global": "python-jobspy",
    "linkedin-israel": "python-jobspy",
    "secrettelaviv": "playwright",
}


def test_registry_order():
    assert list(scraper.ALL_SOURCES.keys()) == EXPECTED_ORDER
    assert len(scraper.ALL_SOURCES) == 19


def test_every_source_has_a_description():
    for name in scraper.ALL_SOURCES:
        assert name in scraper.SOURCE_DESCRIPTIONS
        assert scraper.SOURCE_DESCRIPTIONS[name]


def test_optional_dependency_markers():
    for name, cls in scraper.ALL_SOURCES.items():
        expected = OPTIONAL_DEPENDENCIES.get(name)
        assert getattr(cls, "optional_dependency", None) == expected
