"""Skill keyword list and the token-aware matcher.

Matching is token-aware for *every* keyword (audit finding 10): a keyword must
start and end on a word boundary, so ``core database`` no longer counts as
``core data``. A short list of inflectional suffixes is tolerated after the final
token so ordinary English variation still matches (``remotely``, ``app stores``)
— an arbitrary continuation like ``base`` is not one of them.

Some keywords also have surface aliases the canonical spelling misses
(``CoreData``, a bare ``Combine``); they are declared in ``_EXTRA_PATTERNS`` and
report as their canonical keyword, so ``matched_skills`` stays stable.
"""
import re

SKILL_KEYWORDS = [
    "ios",
    "ipados",
    "macos",
    "iphone",
    "ipad",
    "swift",
    "objective-c",
    "objc",
    "swiftui",
    "uikit",
    "appkit",
    "xcode",
    "apple developer",
    "apple platform",
    "watchos",
    "tvos",
    "cocoa",
    "cocoa touch",
    "core data",
    "combine framework",
    "swift concurrency",
    "app store",
]

# The only inflection allowed after a keyword's last token: a plural, so
# "app stores" and "apple developers" still match. Adverb/participle endings are
# deliberately NOT here — "-ly" would make "swiftly" match "swift", which is the
# false positive the old boundary list existed to prevent. Any other wanted
# variant ("remotely", "asynchronous") is spelled out in its keyword set, where
# it is visible and testable rather than implied by a regex.
_INFLECTIONS = r"(?:s|es)?"

# Aliases: extra spellings that should report as the canonical keyword.
_ALIASES = {
    # Apple's docs write "Core Data"; job ads very often write "CoreData".
    "core data": ("coredata",),
}

# "Combine" is also an everyday English verb ("we combine design and
# engineering"), so a bare mention only counts as Apple's Combine framework when
# another Apple signal sits within a few tokens — the usual "Swift, SwiftUI,
# Combine" skills list. Without this guard, adding plain "combine" as a keyword
# would hand skills_filter an Apple "signal" on prose that has nothing to do
# with Apple platforms.
#
# Bare "swift" is deliberately NOT a licensing signal here: it is ambiguous on
# its own ("swift decisions", the SWIFT payment network), and "we combine data
# and swift delivery" is exactly the sentence this guard exists to reject. Real
# Apple postings that list Combine name SwiftUI/UIKit/iOS beside it anyway.
_APPLE_CONTEXT = (
    r"(?:swiftui|uikit|appkit|xcode|ios|ipados|macos|watchos|tvos|objective-c|objc|cocoa)"
)
_COMBINE_IN_APPLE_CONTEXT = re.compile(
    r"\bcombine\b(?:\W+\w+){0,4}\W+" + _APPLE_CONTEXT + r"\b"
    r"|" + _APPLE_CONTEXT + r"\b(?:\W+\w+){0,4}\W+combine\b",
    re.IGNORECASE,
)

_EXTRA_PATTERNS = {
    "combine framework": (_COMBINE_IN_APPLE_CONTEXT,),
}


def _token_pattern(keyword):
    """Compile ``keyword`` as a boundary-anchored pattern with tolerated inflections."""
    return re.compile(r"\b" + re.escape(keyword) + _INFLECTIONS + r"\b", re.IGNORECASE)


# Patterns are cached per keyword because match_keywords is called with several
# different keyword sets per job, across thousands of jobs per run. Seeded with
# the skill list and its aliases; any other set (remote/onsite/stack keywords in
# filters.rules) is compiled on first use.
_PATTERNS = {keyword: _token_pattern(keyword) for keyword in SKILL_KEYWORDS}
for _canonical, _alias_forms in _ALIASES.items():
    _EXTRA_PATTERNS.setdefault(_canonical, ())
    _EXTRA_PATTERNS[_canonical] += tuple(_token_pattern(alias) for alias in _alias_forms)


def _pattern_for(keyword):
    pattern = _PATTERNS.get(keyword)
    if pattern is None:
        pattern = _PATTERNS[keyword] = _token_pattern(keyword)
    return pattern


def match_keywords(text, keywords):
    """Return the keywords present in ``text``, in the order given.

    A keyword matches on token boundaries (plus its aliases, if any); the result
    always names the canonical keyword, never the alias that matched.
    """
    matches = []
    for keyword in keywords:
        if _pattern_for(keyword).search(text) or any(
            pattern.search(text) for pattern in _EXTRA_PATTERNS.get(keyword, ())
        ):
            matches.append(keyword)
    return matches
