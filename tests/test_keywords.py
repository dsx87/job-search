"""Tests for match_keywords (token-aware matching, plurals, aliases)."""
# --- modules under test (repoint on migration) ---
from job_search.filters.keywords import match_keywords


def test_keyword_requires_word_boundary():
    assert match_keywords("ios developer", ["ios"]) == ["ios"]
    assert match_keywords("kubios biosignal", ["ios"]) == []  # no word boundary
    assert match_keywords("swiftly typed", ["swift"]) == []   # "-ly" is not an inflection


def test_multiword_keyword_is_token_aware_not_substring():
    # audit finding 10: "core database" is not "core data". This was a plain
    # substring match until 2026-07-25.
    assert match_keywords("uses core database internally", ["core data"]) == []
    assert match_keywords("persisted with core data", ["core data"]) == ["core data"]


def test_plural_inflection_still_matches():
    assert match_keywords("shipped to the app stores", ["app store"]) == ["app store"]
    assert match_keywords("apple developers program", ["apple developer"]) == ["apple developer"]
    assert match_keywords("apple developer program", ["apple developer"]) == ["apple developer"]


def test_coredata_alias_reports_the_canonical_keyword():
    # Job ads write "CoreData" at least as often as Apple's "Core Data".
    assert match_keywords("experience with coredata", ["core data"]) == ["core data"]
    assert match_keywords("experience with CoreData", ["core data"]) == ["core data"]


def test_bare_combine_matches_only_near_an_apple_signal():
    # "Combine" is an ordinary English verb, so it counts as the framework only
    # in Apple company — otherwise it would hand skills_filter a bogus signal.
    assert match_keywords("swift, swiftui, combine", ["combine framework"]) == ["combine framework"]
    assert match_keywords("combine and uikit experience", ["combine framework"]) == [
        "combine framework"
    ]
    assert match_keywords("we combine design and business strategy", ["combine framework"]) == []
    # Bare "swift" is too ambiguous to license it (SWIFT payments, "swift
    # delivery"), so this stays a non-match.
    assert match_keywords("we combine data and swift decisions", ["combine framework"]) == []


def test_combine_framework_still_matches_spelled_out():
    assert match_keywords("uses the combine framework", ["combine framework"]) == [
        "combine framework"
    ]


def test_match_keywords_preserves_order_and_collects_all():
    assert match_keywords("ios swift uikit", ["ios", "swift", "uikit"]) == ["ios", "swift", "uikit"]
    assert match_keywords("nothing relevant", ["ios", "swift"]) == []
