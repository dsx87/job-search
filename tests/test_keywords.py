"""Characterization tests for match_keywords (boundary vs substring)."""
# --- modules under test (repoint on migration) ---
from job_search.filters.keywords import match_keywords


def test_boundary_keyword_requires_word_boundary():
    # "ios" is a boundary keyword: matches as a whole word only.
    assert match_keywords("ios developer", ["ios"]) == ["ios"]
    assert match_keywords("kubios biosignal", ["ios"]) == []  # no word boundary
    assert match_keywords("swiftly typed", ["swift"]) == []   # "swift" boundary


def test_substring_keyword_matches_anywhere():
    # "core data" is NOT a boundary keyword: plain substring match.
    assert match_keywords("uses core database internally", ["core data"]) == ["core data"]
    assert match_keywords("apple developer program", ["apple developer"]) == ["apple developer"]


def test_match_keywords_preserves_order_and_collects_all():
    assert match_keywords("ios swift uikit", ["ios", "swift", "uikit"]) == ["ios", "swift", "uikit"]
    assert match_keywords("nothing relevant", ["ios", "swift"]) == []
