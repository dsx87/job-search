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
