"""Characterization tests for HTML/text helpers."""
# --- modules under test (repoint on migration) ---
from job_search.text import (
    collapse_ws,
    strip_html,
    unescape2,
    clean_fragment_text,
    extract_attr,
    section_aware_excerpt,
    is_probably_english,
)

_ENGLISH = (
    "We are looking for a senior iOS engineer to join our team. "
    "You will build and maintain features for our mobile applications using Swift and SwiftUI. "
    "The ideal candidate has experience with UIKit and knows how to work with REST APIs. "
    "This is a fully remote position and we offer visa sponsorship for the right person."
)
_GERMAN = (
    "Wir suchen einen erfahrenen iOS-Entwickler für unser Team in Berlin. "
    "Sie entwickeln und pflegen Funktionen für unsere mobilen Anwendungen mit Swift und SwiftUI. "
    "Der ideale Kandidat hat Erfahrung mit UIKit und kennt sich mit modernen Schnittstellen aus. "
    "Dies ist eine unbefristete Vollzeitstelle vor Ort in unserem modernen Büro."
)
_FRENCH = (
    "Nous recherchons un ingénieur iOS senior pour rejoindre notre équipe à Paris. "
    "Vous développerez et maintiendrez des fonctionnalités pour nos applications mobiles avec Swift. "
    "Le candidat idéal possède une expérience avec UIKit et connaît les interfaces REST. "
    "Il s'agit d'un poste à temps plein sur site dans nos bureaux."
)
_HEBREW = (
    "אנחנו מחפשים מהנדס iOS בכיר להצטרף לצוות שלנו בתל אביב. "
    "התפקיד כולל פיתוח ותחזוקה של אפליקציות מובייל עבור לקוחות רבים בארץ ובעולם."
)
_CYRILLIC = (
    "Мы ищем старшего iOS-разработчика в нашу команду в Москве. "
    "Вы будете создавать и поддерживать функции для наших мобильных приложений на Swift."
)


def test_is_probably_english_true_for_english_prose():
    assert is_probably_english(_ENGLISH) is True


def test_is_probably_english_false_for_german_prose():
    assert is_probably_english(_GERMAN) is False


def test_is_probably_english_false_for_french_prose():
    assert is_probably_english(_FRENCH) is False


def test_is_probably_english_false_for_hebrew_script():
    assert is_probably_english(_HEBREW) is False


def test_is_probably_english_false_for_cyrillic_script():
    assert is_probably_english(_CYRILLIC) is False


def test_is_probably_english_true_for_short_or_empty_text():
    # Biased toward True: too little text to judge → never drop a job.
    assert is_probably_english("Remote iOS role.") is True
    assert is_probably_english("") is True
    assert is_probably_english(None) is True


def test_is_probably_english_true_for_english_prose_dense_with_tech_nouns():
    # Tech nouns appear in postings of every language; they must not drown the
    # English function-word signal in genuine English prose.
    text = (
        "You will work with Swift, SwiftUI, UIKit, Objective-C, Xcode, CocoaPods, "
        "REST, JSON, and the CI and CD tools that our team uses every day. "
        "We expect you to have experience with all of these and to be comfortable "
        "shipping code to production."
    )
    assert is_probably_english(text) is True


def test_collapse_ws_normalizes_whitespace():
    assert collapse_ws("  a   b\n c\t") == "a b c"
    assert collapse_ws(None) == ""
    assert collapse_ws("") == ""


def test_strip_html_removes_tags_and_unescapes_once():
    # tags become spaces, entities are unescaped once; no stripping/collapsing.
    out = strip_html("<p>Hi &amp; <b>bye</b></p>")
    assert out.split() == ["Hi", "&", "bye"]
    assert "&amp;" not in out
    assert "<" not in out
    assert strip_html(None) == ""


def test_unescape2_double_unescapes_only_when_amp_present():
    assert unescape2("&amp;amp;") == "&"
    assert unescape2("&amp;") == "&"
    assert unescape2("plain text") == "plain text"


def test_clean_fragment_text_strips_and_unescapes():
    assert clean_fragment_text("<b>Hello &amp; Co</b>  ") == "Hello & Co"
    assert clean_fragment_text("") == ""


def test_extract_attr():
    assert extract_attr('href="https://x.com/a?b=1&amp;c=2"', "href") == "https://x.com/a?b=1&c=2"
    assert extract_attr('HREF="/y"', "href") == "/y"  # case-insensitive
    assert extract_attr("", "href") == ""
    assert extract_attr('class="z"', "href") == ""


# --- audit order 5: section_aware_excerpt (Finding 11) ---
def test_section_aware_excerpt_returns_short_text_unchanged():
    assert section_aware_excerpt("short text", 100) == "short text"


def test_section_aware_excerpt_non_positive_limit_returns_empty():
    assert section_aware_excerpt("anything at all", 0) == ""
    assert section_aware_excerpt("anything at all", -5) == ""


def test_section_aware_excerpt_respects_limit_when_truncating():
    text = "word " * 1000  # 5000 chars, no eligibility/location keywords
    result = section_aware_excerpt(text, 200)
    assert len(result) <= 200


def test_section_aware_excerpt_preserves_head_prefix():
    head = "SUMMARY: this is the head portion of the posting. "
    text = head + ("more text here. " * 500)
    result = section_aware_excerpt(text, 500)
    assert result.startswith(text[:50])
    assert len(result) <= 500


def test_section_aware_excerpt_surfaces_late_restriction():
    text = (
        "SUMMARY. "
        + ("filler words here. " * 400)
        + " Eligibility: US residents only; visa sponsorship is not available."
    )
    limit = 1500
    # precondition: a naive text[:limit] prefix cut would drop the late restriction
    assert "residents only" not in text[:limit]
    result = section_aware_excerpt(text, limit)
    assert ("residents only" in result) or ("sponsorship" in result)
    assert "SUMMARY" in result
    assert len(result) <= limit


def test_section_aware_excerpt_is_deterministic():
    text = (
        "SUMMARY. "
        + ("filler words here. " * 400)
        + " visa sponsorship is not available."
    )
    assert section_aware_excerpt(text, 1500) == section_aware_excerpt(text, 1500)
