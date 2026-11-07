"""Text area: the engine's text helpers and the Python string semantics under them.

``edge-strings.json`` runs every edge string through the engine helpers
(``strip_html``, ``html.unescape``, ``clean_job_description``, ``collapse_ws``,
``_normalize_content``) and through the Python primitives they rest on
(``str.strip``, ``str.lower``, ``str.split``, ``re`` ``\\s`` and ``\\d``), so the
Swift ``PyText`` helper can be checked against each.

``html5-entities.json`` is ``html.entities.html5`` as Python has it;
``html-charrefs.json`` holds the two private tables ``html.unescape`` uses for
numeric references, so a Swift port can copy them instead of retyping them.

``unicode-classes.json`` enumerates every code point of Python's Unicode
database that ``str.isspace`` (``strip``/``split``), ``re`` ``\\s`` and ``re`` ``\\d``
accept, as inclusive ``[first, last]`` ranges.
"""
import html
import html.entities
import re
import sys
import unicodedata

from job_search.pipeline.stages import clean_job_description
from job_search.state.seen_jobs import _normalize_content
from job_search.text import collapse_ws, strip_html

NOTES = [
    "text: html-charrefs.json copies html._invalid_charrefs (0x00 and 0x80-0x9F remaps) and "
    "html._invalid_codepoints (dropped code points); html.unescape maps surrogates and "
    "code points above 0x10FFFF to U+FFFD.",
    "text: unicode-classes.json lists inclusive [first, last] code point ranges for Python's "
    "Unicode database version recorded in the file.",
]

_ENTITY_CASES = [
    ("named_with_semicolon", "Tom &amp; Jerry"),
    ("named_without_semicolon_legacy", "Tom &amp Jerry"),
    ("named_without_semicolon_prefix", "&ampx &copy2026"),
    ("named_uppercase_legacy", "&AMP; &LT; &GT;"),
    ("named_longest_prefix", "&notit; &notin; &notin"),
    ("named_unknown", "&NotAnEntity; &bogus"),
    ("named_multi_codepoint", "&NotEqualTilde; &fjlig;"),
    ("nbsp_entity", "iOS&nbsp;Engineer&nbsp"),
    ("decimal_with_semicolon", "&#65;&#66;&#67;"),
    ("decimal_without_semicolon", "&#65 &#66x"),
    ("hex_lower_and_upper", "&#x41; &#X42; &#x43"),
    ("hex_letters_case", "&#xe9; &#XE9;"),
    ("numeric_zero", "a&#0;b&#x0;c"),
    ("numeric_surrogates", "&#xD800;&#xDFFF;&#55296;"),
    ("numeric_above_max", "&#x110000;&#99999999999;"),
    ("numeric_c1_remapped", "&#128;&#x80;&#x85;&#x8D;&#x9F;&#150;"),
    ("numeric_invalid_codepoints", "a&#1;b&#x0B;c&#x7F;d&#xFDD0;e&#xFFFE;f&#x10FFFF;g"),
    ("numeric_control_allowed", "a&#9;b&#10;c&#13;d"),
    ("numeric_emoji", "&#x1F600; &#128512;"),
    ("numeric_empty", "&#; &#x; &#xZ;"),
    ("double_encoded", "&amp;lt;p&amp;gt;"),
    ("lone_ampersand", "R&D & more &"),
]

_WHITESPACE_CASES = [
    ("ascii_whitespace", " \t\n\r\x0b\x0c a \t b \r\n"),
    ("nbsp", " iOS  Engineer "),
    ("line_separator", "Berlin Germany "),
    ("information_separators", "\x1ca\x1db\x1ec\x1f"),
    ("next_line", "\x85a\x85b\x85"),
    ("ideographic_space", "　Tokyo　"),
    ("other_unicode_spaces", "a b c d e f"),
    ("not_python_whitespace", "a​b﻿c᠎d⁠e"),
    ("only_whitespace", "  \x1c \t"),
    ("empty", ""),
]

_CASE_CASES = [
    ("turkish_dotted_i", "İSTANBUL İstanbul"),
    ("turkish_dotless_i", "ı I i"),
    ("sharp_s", "Straße STRASSE ẞ"),
    ("final_sigma", "ΟΔΟΣ Σ"),
    ("ligatures_and_titlecase", "ﬁ ǅ Ǆ"),
    ("greek_and_cyrillic", "ΑΒΓ ЖИВ"),
]

_GRAPHEME_CASES = [
    ("emoji_simple", "Ship it \U0001f680"),
    ("emoji_zwj_sequence", "Dev \U0001f469‍\U0001f4bb team \U0001f468‍\U0001f469‍\U0001f467"),
    ("emoji_flag_and_skin_tone", "\U0001f1e9\U0001f1ea \U0001f44d\U0001f3fd"),
    ("combining_acute", "Café Zé́"),
    ("precomposed_vs_decomposed", "Å Å Å"),
    ("combining_after_space", "a ́b"),
]

_DIGIT_CASES = [
    ("ascii_digits", "iOS 15, 3+ years, $90k, Over 214 applicants"),
    ("arabic_indic_digits", "٣ ١٢ years"),
    ("devanagari_digits", "१२ ३"),
    ("fullwidth_digits", "１２３ yen"),
    ("superscript_and_fractions", "x² ½ ①"),
    ("roman_numeral", "Engineer Ⅻ"),
    ("mixed_digits", "v2٣４"),
]

_HTML_CASES = [
    ("paragraph", "<p>Hello <b>world</b></p>"),
    ("self_closing", "line<br/>break<br>here"),
    ("uppercase_tags", "<DIV>Upper</DIV>"),
    ("multiline_tag", "<p\nclass=\"x\">text</p>"),
    ("attribute_with_gt", "<a href='x>y'>link</a>"),
    ("comment", "a<!-- note -->b"),
    ("script", "<script>var a = 1 < 2;</script>after"),
    ("lone_angles", "a < b > c"),
    ("unclosed_tag", "text <unclosed"),
    ("empty_tag", "a<>b"),
    ("generics_plain_text", "Map<String, Int> and Array<T>"),
    ("markdown_autolink", "Apply at <https://apply.example.com/jobs/1>"),
    ("escaped_markup", "&lt;p&gt;Hello&lt;/p&gt;"),
    ("entity_triggers_markup", "Map<String, Int> &amp; more"),
    ("hex_entity_triggers_markup", "Caf&#xe9; <i>x</i>"),
    ("unknown_named_entity_triggers_markup", "Map<K> &zz;"),
    ("tags_and_whitespace", "<ul>\n  <li>Swift</li> <li>SwiftUI</li> </ul>"),
    ("tags_and_digits", "<p>iOS 17 &amp; ٣ years</p>"),
]

EDGE_CASES = _ENTITY_CASES + _WHITESPACE_CASES + _CASE_CASES + _GRAPHEME_CASES + _DIGIT_CASES + _HTML_CASES


def _edge_strings():
    names = [name for name, _ in EDGE_CASES]
    assert len(names) == len(set(names)), "duplicate case name"
    return [
        {
            "name": name,
            "input": value,
            "strip_html": strip_html(value),
            "html_unescape": html.unescape(value),
            "clean_job_description": clean_job_description(value),
            "collapse_ws": collapse_ws(value),
            "normalize_content": _normalize_content(value),
            "str_strip": value.strip(),
            "str_lower": value.lower(),
            "str_split": value.split(),
            "re_whitespace_findall": re.findall(r"\s", value),
            "re_whitespace_runs_to_space": re.sub(r"\s+", " ", value),
            "re_digit_findall": re.findall(r"\d", value),
            "re_digit_runs_to_space": re.sub(r"\d+", " ", value),
        }
        for name, value in EDGE_CASES
    ]


def _ranges(predicate):
    ranges = []
    for code in range(sys.maxunicode + 1):
        if predicate(chr(code)):
            if ranges and ranges[-1][1] == code - 1:
                ranges[-1][1] = code
            else:
                ranges.append([code, code])
    return ranges


def _unicode_classes():
    whitespace_re = re.compile(r"\s")
    digit_re = re.compile(r"\d")
    return {
        "unicode_version": unicodedata.unidata_version,
        "str_isspace": _ranges(str.isspace),
        "re_whitespace": _ranges(lambda char: whitespace_re.fullmatch(char) is not None),
        "re_digit": _ranges(lambda char: digit_re.fullmatch(char) is not None),
    }


def export():
    return {
        "edge-strings.json": _edge_strings(),
        "html5-entities.json": dict(html.entities.html5),
        "html-charrefs.json": {
            "invalid_charrefs": {str(code): char for code, char in html._invalid_charrefs.items()},
            "invalid_codepoints": sorted(html._invalid_codepoints),
        },
        "unicode-classes.json": _unicode_classes(),
    }
