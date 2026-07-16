"""audit order 8 — JobSpy indeed/google query reduction.

The JobSpy (indeed/google) search fans out over
``SEARCH_QUERIES × COUNTRY_SEARCHES × JOBSPY_SITES``. Trimming SEARCH_QUERIES
from 5 to at most 3 broad, non-overlapping queries cuts the per-run request
count substantially, while COUNTRY_SEARCHES is left untouched so every EU
relocation target is still covered.
"""
from job_search.sources.jobspy_sources import (
    COUNTRY_SEARCHES,
    JOBSPY_SITES,
    SEARCH_QUERIES,
)

# The pre-reduction fan-out this change is trimming.
_OLD_SEARCH_COUNT = 5 * 12 * 2  # == 120


def test_search_queries_reduced_to_at_most_three():
    assert len(SEARCH_QUERIES) <= 3
    # each remaining query is a non-empty string
    assert all(isinstance(q, str) and q.strip() for q in SEARCH_QUERIES)
    # non-overlapping: no duplicate queries
    assert len(set(SEARCH_QUERIES)) == len(SEARCH_QUERIES)


def test_effective_jobspy_search_count_drops_substantially():
    effective = len(SEARCH_QUERIES) * len(COUNTRY_SEARCHES) * len(JOBSPY_SITES)
    # documents the reduction: well below the old 5×12×2 fan-out
    assert effective <= 3 * len(COUNTRY_SEARCHES) * len(JOBSPY_SITES)
    assert effective < _OLD_SEARCH_COUNT


def test_country_searches_unchanged_and_cover_eu_relocation_targets():
    # COUNTRY_SEARCHES is explicitly left unchanged by this reduction.
    assert len(COUNTRY_SEARCHES) == 12
    countries = {next(iter(entry.values())) for entry in COUNTRY_SEARCHES}
    for target in ("Germany", "Netherlands", "Portugal", "Spain", "Ireland", "France"):
        assert target in countries
