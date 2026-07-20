"""HTML/text helpers: tag stripping, entity unescaping, attribute extraction."""
import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# High-frequency English function words whose DENSITY distinguishes English
# prose from other languages. Deliberately excludes short forms that other
# European languages share ("in"/"an" German, "a"/"on" French/Spanish) so
# non-English prose scores near zero. Tech nouns ("iOS", "Swift") are language-
# neutral and intentionally absent.
_ENGLISH_FUNCTION_WORDS = frozenset(
    "the and of to for with you your our we are is be it that this these those "
    "they them their will would should can could has have had not from what "
    "which who when where about all more also into over per".split()
)
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)  # any Unicode letter
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z]+")


def is_probably_english(text, min_words=20, min_ratio=0.06) -> bool:
    """Heuristic language gate — True unless the text is CLEARLY not English.

    Biased toward True so a genuine English posting is never dropped. Returns
    False only when the text is script-dominated by non-Latin letters
    (Hebrew/Cyrillic/CJK/Arabic) OR has enough words to judge and almost no
    English function words (German/French/Spanish/… prose). Deterministic and
    dependency-free.
    """
    text = str(text or "")
    letters = _LETTER_RE.findall(text)
    if not letters:
        return True  # nothing to judge — don't drop
    latin = _LATIN_LETTER_RE.findall(text)
    if len(latin) / len(letters) < 0.5:
        return False  # majority non-Latin script → not English
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < min_words:
        return True  # too little Latin text to judge confidently — don't drop
    hits = sum(1 for w in words if w in _ENGLISH_FUNCTION_WORDS)
    return hits / len(words) >= min_ratio


def collapse_ws(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_html(text):
    return html.unescape(_HTML_TAG_RE.sub(" ", str(text or "")))


def unescape2(text):
    result = html.unescape(str(text or ""))
    if "&" in result:
        result = html.unescape(result)
    return result


def clean_fragment_text(value):
    return html.unescape(strip_html(value)).strip()


def extract_attr(attrs, name):
    pattern = re.compile(
        r"\b" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(attrs or "")
    return html.unescape(match.group(2)) if match else ""


# Eligibility / authorization / location / working-arrangement cues that often
# decide a fit and frequently appear late in a long posting.
_EXCERPT_KEYWORDS = (
    "sponsor", "visa", "authoriz", "work permit", "residents only",
    "citizen", "clearance", "eligible to work", "must be located",
    "must reside", "relocat", "remote", "on-site", "onsite", "hybrid",
    "time zone", "timezone", "overlap", "in-person", "in person",
)


def section_aware_excerpt(text, limit):
    """Truncate to ``limit`` chars while surfacing late eligibility restrictions.

    Naive prefix truncation drops requirements that appear near the end of long
    postings ("US residents only", "visa sponsorship not available"). This keeps
    a head portion (the summary) and appends windows around important keywords
    found beyond it, so those restrictions still reach the AI. Deterministic;
    returns ``text`` unchanged when it already fits.
    """
    text = str(text or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    tail_budget = limit // 3
    head_len = limit - tail_budget
    head = text[:head_len]
    remainder = text[head_len:]
    low = remainder.lower()
    windows = []
    for keyword in _EXCERPT_KEYWORDS:
        start = 0
        while True:
            i = low.find(keyword, start)
            if i == -1:
                break
            windows.append((max(0, i - 80), min(len(remainder), i + len(keyword) + 160)))
            start = i + len(keyword)
    if not windows:
        return head
    windows.sort()
    merged = []
    for a, b in windows:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    marker = " … "
    picked = []
    used = 0
    for a, b in merged:
        snippet = remainder[a:b].strip()
        if not snippet:
            continue
        need = len(snippet) + len(marker)
        if used + need > tail_budget:
            remaining = tail_budget - used - len(marker)
            if remaining > 20:
                picked.append(snippet[:remaining])
            break
        picked.append(snippet)
        used += need
    if not picked:
        return head
    return (head + marker + marker.join(picked))[:limit]
