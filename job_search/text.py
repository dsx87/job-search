"""HTML/text helpers: tag stripping, entity unescaping, attribute extraction."""
import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")


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
