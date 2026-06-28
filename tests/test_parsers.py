"""Characterization tests for the standalone parsing helpers."""
import datetime as dt

# --- modules under test (repoint on migration) ---
from job_search.sources.parsers import (
    split_title_company,
    extract_location,
    parse_rss_jobs,
    parse_link_jobs,
    decode_mobilecareer_job_object,
    mobilecareer_date,
    mobilecareer_location,
    mobilecareer_description,
    jobscroller_relative_date,
    jobscroller_card_values,
)


def test_split_title_company():
    assert split_title_company("iOS Dev at Acme") == ("iOS Dev", "Acme")
    assert split_title_company("Acme: iOS Dev") == ("iOS Dev", "Acme")
    assert split_title_company("Just a Title") == ("Just a Title", "")


def test_extract_location():
    assert extract_location("Location: Berlin, Germany\nrest of text") == "Berlin, Germany"
    assert extract_location("We are based in Amsterdam, hiring now") == "Amsterdam"
    assert extract_location("No location at all here") == "Remote"
    assert extract_location("No location at all here", default="Anywhere") == "Anywhere"


RSS = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>iOS Dev at Acme</title>
    <description>Location: Berlin
Great role</description>
    <link>https://x/1</link>
    <pubDate>Mon, 15 Mar 2024 10:00:00 +0000</pubDate>
    <category>iOS</category>
  </item>
</channel></rss>"""


def test_parse_rss_jobs():
    jobs = parse_rss_jobs(RSS, source="mysrc")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "iOS Dev"
    assert j.company == "Acme"
    assert j.location == "Berlin"
    assert j.url == "https://x/1"
    assert j.source == "mysrc"
    assert j.date_posted == dt.date(2024, 3, 15)
    assert j.is_remote is True
    assert j.description == "Location: Berlin\nGreat role iOS"


def test_parse_link_jobs():
    html = (
        '<a href="/job/123">iOS Dev at Acme</a>'
        '<a href="/about">About us page</a>'
        '<a href="/job/123">iOS Dev at Acme</a>'  # dup url
    )
    jobs = parse_link_jobs(html, "https://r.me", ["/job/"], "relocate.me")
    assert len(jobs) == 1
    assert jobs[0].url == "https://r.me/job/123"
    assert jobs[0].title == "iOS Dev"
    assert jobs[0].company == "Acme"
    assert jobs[0].source == "relocate.me"


def test_decode_mobilecareer_job_object_plain_and_escaped():
    assert decode_mobilecareer_job_object('{"id":"1","title":"iOS"}')["id"] == "1"
    escaped = r'{\"id\":\"2\",\"title\":\"macOS\"}'
    assert decode_mobilecareer_job_object(escaped)["id"] == "2"
    assert decode_mobilecareer_job_object("not json") is None


def test_mobilecareer_date():
    assert mobilecareer_date("$D2024-03-15T00:00:00Z") == dt.date(2024, 3, 15)
    assert mobilecareer_date("2024-03-15") == dt.date(2024, 3, 15)
    assert mobilecareer_date(None) is None


def test_mobilecareer_location():
    assert mobilecareer_location({"location": ["Berlin", "Germany"], "locationType": "remote"}) == "Remote - Berlin, Germany"
    assert mobilecareer_location({"location": []}) == "Remote"
    assert mobilecareer_location({"location": ["NYC"], "locationType": "onsite"}) == "Onsite - NYC"


def test_mobilecareer_description():
    out = mobilecareer_description({"role": "iOS Dev", "skills": ["Swift", "UIKit"], "salaryMin": 100})
    assert out == "iOS Dev Swift UIKit Salary 100-"


def test_jobscroller_relative_date():
    today = dt.date.today()
    assert jobscroller_relative_date("3d ago") == today - dt.timedelta(days=3)
    assert jobscroller_relative_date("2h ago") == today
    assert jobscroller_relative_date("1w ago") == today - dt.timedelta(days=7)
    assert jobscroller_relative_date("nope") is None


def test_jobscroller_card_values():
    card = (
        '<p class="a">Acme Inc</p>'
        '<p class="b">iOS Developer</p>'
        '<div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-slate-500">'
        '<span>Berlin</span><span>·</span><span>3d ago</span></div>'
        '<div class="hidden sm:flex flex-wrap gap-1 justify-end max-w-[200px] shrink-0">'
        '<span>Swift</span></div>'
    )
    company, title, location, relative_date, description = jobscroller_card_values(card)
    assert company == "Acme Inc"
    assert title == "iOS Developer"
    assert location == "Berlin"
    assert relative_date == "3d ago"
    assert "Swift" in description
