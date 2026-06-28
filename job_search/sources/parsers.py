"""Shared parsing helpers for the HTML/RSS/text sources."""
import datetime as dt
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..dates import parse_iso_date, parse_rss_date
from ..location.classify import guess_location_from_text
from ..models import Job
from ..text import clean_fragment_text, collapse_ws, extract_attr, strip_html

# ── RSS / generic ──────────────────────────────────────────────────────────────
_LOCATION_RE = re.compile(r"(?:^|\n)\s*Location:\s*(.+)")


def split_title_company(title):
    title = collapse_ws(title)
    if " at " in title:
        job_title, company = title.rsplit(" at ", 1)
        return job_title.strip(), company.strip()
    if ": " in title:
        company, job_title = title.split(": ", 1)
        return job_title.strip(), company.strip()
    return title.strip(), ""


def extract_location(description, default="Remote"):
    text = strip_html(description)
    match = _LOCATION_RE.search(text)
    if match:
        return match.group(1).splitlines()[0].strip() or default
    guessed = guess_location_from_text(text)
    return guessed or default


def parse_rss_jobs(xml_text, source, default_location="Remote", is_remote=True):
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        title, company = split_title_company(raw_title)
        description = item.findtext("description") or ""
        categories = []
        for element in item.findall("category"):
            if element.text:
                categories.append(element.text.strip())
        jobs.append(
            Job(
                title=title,
                company=company,
                location=extract_location(description, default=default_location),
                url=(item.findtext("link") or "").strip(),
                source=source,
                date_posted=parse_rss_date(item.findtext("pubDate") or ""),
                description="{} {}".format(description, " ".join(categories)).strip(),
                is_remote=is_remote,
            )
        )
    return jobs


# ── mobile.career ────────────────────────────────────────────────────────────
MOBILECAREER_JOB_OBJECT_RE = re.compile(
    r'\{\\?"id\\?":\\?"[^"\\]+\\?",\\?"applyType\\?":.*?\\?"paymentStatus\\?":\\?"[^"\\]+\\?"\}',
    re.DOTALL,
)


def decode_mobilecareer_job_object(raw):
    try:
        if '\\"' in raw:
            return json.loads(json.loads('"{}"'.format(raw)))
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def mobilecareer_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("$D"):
        text = text[2:]
    return parse_iso_date(text)


def mobilecareer_location(item):
    raw_parts = item.get("location") or []
    if not isinstance(raw_parts, list):
        raw_parts = [raw_parts]

    parts = []
    seen = set()
    for raw in raw_parts:
        part = str(raw or "").strip(" ,")
        if not part or part.lower() in seen:
            continue
        seen.add(part.lower())
        parts.append(part)

    location_type = str(item.get("locationType") or "").strip().title()
    prefix = location_type or "Remote"
    if not parts:
        return prefix
    return "{} - {}".format(prefix, ", ".join(parts))


def mobilecareer_description(item):
    skills = item.get("skills") or []
    if isinstance(skills, list):
        skills_text = " ".join(str(skill) for skill in skills if skill)
    else:
        skills_text = str(skills)

    parts = [
        item.get("role"),
        item.get("employmentType"),
        item.get("seniority"),
        item.get("companyTagline"),
        skills_text,
    ]
    salary_min = item.get("salaryMin")
    salary_max = item.get("salaryMax")
    if salary_min or salary_max:
        parts.append("Salary {}-{}".format(salary_min or "", salary_max or ""))
    return " ".join(str(part) for part in parts if part).strip()


# ── jobscroller ────────────────────────────────────────────────────────────────
JOBSCROLLER_CARD_RE = re.compile(
    r'<a class="block bg-slate-900 border border-slate-800 rounded-xl p-5[^"]*" href="(/jobs/\d+)">(.*?)</a>',
    re.DOTALL,
)
JOBSCROLLER_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
JOBSCROLLER_META_RE = re.compile(
    r'<div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-slate-500">(.*?)</div>',
    re.DOTALL,
)
JOBSCROLLER_TAGS_RE = re.compile(
    r'<div class="hidden sm:flex flex-wrap gap-1 justify-end max-w-\[200px\] shrink-0">(.*?)</div>',
    re.DOTALL,
)
JOBSCROLLER_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.DOTALL)
JOBSCROLLER_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*(m|h|d|w|mo|y)\s+ago", re.IGNORECASE)
JOBSCROLLER_SALARY_RE = re.compile(r"[$€£]\S+")


def jobscroller_relative_date(value):
    text = value.strip().lower()
    match = JOBSCROLLER_RELATIVE_DATE_RE.search(text)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit in ("m", "h"):
        days = 0
    elif unit == "d":
        days = amount
    elif unit == "w":
        days = amount * 7
    elif unit == "mo":
        days = amount * 30
    else:
        days = amount * 365
    return dt.date.today() - dt.timedelta(days=days)


def jobscroller_card_values(card_html):
    paragraphs = [clean_fragment_text(value) for value in JOBSCROLLER_PARAGRAPH_RE.findall(card_html)]
    company = paragraphs[0] if len(paragraphs) >= 1 else ""
    title = paragraphs[1] if len(paragraphs) >= 2 else ""

    meta_values = []
    meta_match = JOBSCROLLER_META_RE.search(card_html)
    if meta_match:
        meta_values = [
            clean_fragment_text(value)
            for value in JOBSCROLLER_SPAN_RE.findall(meta_match.group(1))
        ]
        meta_values = [value for value in meta_values if value and value != "·"]

    location = meta_values[0] if meta_values else ""
    relative_date = ""
    for value in meta_values[1:]:
        if "ago" in value.lower():
            relative_date = value
            break

    tags = []
    tag_match = JOBSCROLLER_TAGS_RE.search(card_html)
    if tag_match:
        tags = [
            clean_fragment_text(value)
            for value in JOBSCROLLER_SPAN_RE.findall(tag_match.group(1))
        ]
        tags = [tag for tag in tags if tag]

    description_parts = tags + [
        value for value in meta_values if not JOBSCROLLER_SALARY_RE.search(value)
    ]
    description = " ".join(part for part in description_parts if part)
    return company, title, location, relative_date, description


# ── link-scraping (relocate.me, secrettelaviv) ─────────────────────────────────
_A_TAG_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)


def parse_link_jobs(html_text, base_url, href_fragments, source_name, default_remote=False):
    jobs = []
    seen = set()
    for match in _A_TAG_RE.finditer(html_text):
        href = extract_attr(match.group("attrs"), "href")
        if not href or not any(fragment in href for fragment in href_fragments):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        text = collapse_ws(strip_html(match.group("body")))
        if not text or len(text) < 3:
            continue
        title, company = split_title_company(text)
        title = title[:140]
        location = guess_location_from_text(text)
        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                url=url,
                source=source_name,
                description=text,
                is_remote=default_remote,
            )
        )
    return jobs
