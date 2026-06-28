"""Characterization tests for HTML/text helpers."""
# --- modules under test (repoint on migration) ---
from job_search.text import (
    collapse_ws,
    strip_html,
    unescape2,
    clean_fragment_text,
    extract_attr,
)


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
