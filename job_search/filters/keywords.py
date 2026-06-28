"""Skill keyword list and the boundary-vs-substring matcher."""
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

# Short/ambiguous keywords matched on word boundaries (so "ios" does not match
# inside "bios"); everything else is a plain substring match.
_BOUNDARY_KEYWORDS = set(
    [
        "ios",
        "ipados",
        "iphone",
        "ipad",
        "macos",
        "swift",
        "swiftui",
        "uikit",
        "appkit",
        "xcode",
        "watchos",
        "tvos",
        "cocoa",
        "objc",
    ]
)

_SKILL_PATTERNS = {}
for _kw in SKILL_KEYWORDS:
    if _kw in _BOUNDARY_KEYWORDS:
        _SKILL_PATTERNS[_kw] = re.compile(
            r"\b" + re.escape(_kw) + r"\b",
            re.IGNORECASE,
        )


def match_keywords(text, keywords):
    matches = []
    for keyword in keywords:
        if keyword in _BOUNDARY_KEYWORDS:
            pattern = _SKILL_PATTERNS.get(keyword)
            if pattern and pattern.search(text):
                matches.append(keyword)
        elif keyword in text:
            matches.append(keyword)
    return matches
