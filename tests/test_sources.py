"""Characterization tests for each source's parse path.

Network is replaced by monkeypatching each source module's http_json /
http_request binding, so these run fully offline. Optional sources
(jobspy/linkedin/secrettelaviv) are verified to skip gracefully when their
dependency is absent.
"""
import datetime as dt

import pytest

# --- modules under test (repoint on migration) ---
from job_search.models import Job
from job_search.sources import base
from job_search.sources import api_sources, html_sources, rss_sources
from job_search.sources import fetch as fetch_mod
from job_search.sources.api_sources import (
    ArbeitnowSource,
    HimalayasSource,
    JobicySource,
    RemoteOKSource,
    RemotiveSource,
    TheMuseSource,
    WorkingNomadsSource,
)
from job_search.sources.rss_sources import (
    RemoteFirstJobsSource,
    SwissDevJobsSource,
    WeWorkRemotelySource,
)
from job_search.sources.html_sources import (
    ArcSource,
    JobScrollerSource,
    MobileCareerSource,
    RelocateMeSource,
)


def set_json(monkeypatch, payload, status=200):
    monkeypatch.setattr(api_sources, "http_json", lambda url, *a, **k: (status, payload))


def set_text(monkeypatch, text, status=200):
    # http_request is bound into both the rss and html source modules.
    monkeypatch.setattr(rss_sources, "http_request", lambda url, *a, **k: (status, text))
    monkeypatch.setattr(html_sources, "http_request", lambda url, *a, **k: (status, text))


def test_remotive(monkeypatch):
    set_json(monkeypatch, {"jobs": [{
        "title": "iOS Engineer", "company_name": "Acme", "candidate_required_location": "Remote",
        "url": "https://x/1", "publication_date": "2024-03-15", "description": "d", "tags": ["swift"],
    }]})
    jobs = RemotiveSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].is_remote is True
    assert jobs[0].date_posted == dt.date(2024, 3, 15)


def test_remoteok_skips_leading_metadata(monkeypatch):
    set_json(monkeypatch, [
        {"legal": "metadata row"},
        {"position": "iOS Dev", "company": "Acme", "location": "Remote",
         "url": "/remote-jobs/1", "epoch": 1609502400, "tags": ["swift"], "description": "d"},
    ])
    jobs = RemoteOKSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Dev"
    assert jobs[0].url == "https://remoteok.com/remote-jobs/1"  # relative url prefixed


def test_jobicy(monkeypatch):
    set_json(monkeypatch, {"jobs": [{
        "jobTitle": "iOS", "companyName": "Acme", "jobGeo": "Remote",
        "url": "u", "pubDate": "2024-03-15", "jobDescription": "d",
    }]})
    jobs = JobicySource().fetch()
    assert jobs[0].title == "iOS"
    assert jobs[0].company == "Acme"


def test_arbeitnow(monkeypatch):
    set_json(monkeypatch, {"data": [{
        "title": "iOS", "company_name": "Acme", "location": "Berlin",
        "url": "u", "created_at": 1609502400, "description": "d", "remote": True,
    }]})
    jobs = ArbeitnowSource().fetch()
    assert jobs[0].is_remote is True
    assert jobs[0].date_posted == dt.date(2021, 1, 1)


def test_themuse(monkeypatch):
    set_json(monkeypatch, {"results": [{
        "name": "iOS Engineer", "company": {"name": "Acme"},
        "locations": [{"name": "Remote"}], "refs": {"landing_page": "https://x/1"},
        "publication_date": "2024-03-15", "contents": "desc",
        "categories": [{"name": "Engineering"}], "levels": [{"name": "Senior"}],
    }], "page_count": 1})
    jobs = TheMuseSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Engineer"
    assert jobs[0].location == "Remote"


def test_himalayas(monkeypatch):
    set_json(monkeypatch, {"jobs": [{
        "title": "iOS", "companyName": "Acme", "locationRestrictions": ["Germany"],
        "description": "d", "applicationLink": "u", "pubDate": "2024-03-15", "categories": ["mobile"],
    }]})
    jobs = HimalayasSource().fetch()
    assert jobs[0].location == "Germany"


def test_workingnomads(monkeypatch):
    set_json(monkeypatch, {"hits": {"hits": [{"_source": {
        "title": "iOS Engineer", "company": "Acme", "slug": "ios-eng",
        "pub_date": "2024-03-15", "description": "desc", "tags": ["swift"],
        "locations": ["Remote"], "position_type": "Full", "experience_level": "Senior",
    }}]}})
    jobs = WorkingNomadsSource().fetch()
    assert jobs[0].title == "iOS Engineer"
    assert jobs[0].url == "https://www.workingnomads.com/jobs/ios-eng"
    assert jobs[0].date_posted == dt.date(2024, 3, 15)


WWR_RSS = """<?xml version="1.0"?>
<rss><channel><item>
  <title>Acme: iOS Developer</title>
  <link>https://x/1</link>
  <description>desc</description>
  <pubDate>Mon, 15 Mar 2024 10:00:00 +0000</pubDate>
  <region>Europe</region>
  <country>Germany</country>
</item></channel></rss>"""


def test_weworkremotely(monkeypatch):
    set_text(monkeypatch, WWR_RSS)
    jobs = WeWorkRemotelySource().fetch()
    assert len(jobs) == 2  # both feeds, no internal dedup
    assert jobs[0].company == "Acme"
    assert jobs[0].title == "iOS Developer"
    assert jobs[0].location == "Germany"


RFJ_RSS = """<?xml version="1.0"?>
<rss><channel><item>
  <title>iOS Dev at Acme</title>
  <link>https://x/1</link>
  <description>Location: Berlin
desc</description>
  <pubDate>Mon, 15 Mar 2024 10:00:00 +0000</pubDate>
</item></channel></rss>"""


def test_remotefirstjobs(monkeypatch):
    set_text(monkeypatch, RFJ_RSS)
    jobs = RemoteFirstJobsSource().fetch()
    assert jobs[0].title == "iOS Dev"
    assert jobs[0].company == "Acme"


SWISS_RSS = """<?xml version="1.0"?>
<rss><channel><item>
  <title>iOS Developer @ Acme - Berlin</title>
  <link>https://x/1</link>
  <description>desc</description>
  <pubDate>Mon, 15 Mar 2024 10:00:00 +0000</pubDate>
</item></channel></rss>"""


def test_swissdevjobs(monkeypatch):
    set_text(monkeypatch, SWISS_RSS)
    jobs = SwissDevJobsSource().fetch()
    assert jobs[0].title == "iOS Developer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Switzerland"


def test_mobilecareer(monkeypatch):
    obj = ('{"id":"1","applyType":"direct","title":"iOS Engineer",'
           '"companyName":"Acme","locationType":"remote","paymentStatus":"paid"}')
    set_text(monkeypatch, "junk " + obj + " junk")
    jobs = MobileCareerSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].is_remote is True


def test_jobscroller(monkeypatch):
    card_inner = (
        '<p class="a">Acme Inc</p><p class="b">iOS Developer</p>'
        '<div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-slate-500">'
        '<span>Berlin</span><span>3d ago</span></div>'
    )
    html = ('<a class="block bg-slate-900 border border-slate-800 rounded-xl p-5 x" '
            'href="/jobs/42">' + card_inner + "</a>")
    set_text(monkeypatch, html)
    jobs = JobScrollerSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Developer"
    assert jobs[0].company == "Acme Inc"
    assert jobs[0].location == "Berlin"
    assert jobs[0].url == "https://www.jobscroller.net/jobs/42"


def test_arc(monkeypatch):
    next_json = (
        '{"props":{"jobs":[{"randomKey":"abc","title":"iOS Engineer",'
        '"company":{"name":"Acme"},"urlString":"ios-engineer",'
        '"postedAt":1609502400,"requiredCountries":["DE"]}]}}'
    )
    html = ('<script type="application/json" id="__NEXT_DATA__">' + next_json + "</script>")
    set_text(monkeypatch, html)
    jobs = ArcSource().fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "iOS Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Remote - Germany"
    assert "ios-engineer-abc" in jobs[0].url


def test_relocateme_dedups_across_queries(monkeypatch):
    html = '<a href="/job/1">iOS Dev at Acme</a>'
    set_text(monkeypatch, html)
    jobs = RelocateMeSource().fetch()
    assert len(jobs) == 1  # 3 queries, same posting -> deduped
    assert jobs[0].url == "https://relocate.me/job/1"


@pytest.mark.parametrize("module_name,cls_name,dep_modules", [
    ("jobspy_sources", "JobSpySource", ["jobspy"]),
    ("jobspy_sources", "LinkedInGlobalSource", ["jobspy"]),
    ("jobspy_sources", "LinkedInIsraelSource", ["jobspy"]),
    ("playwright_sources", "SecretTelAvivSource", ["playwright", "playwright.sync_api"]),
])
def test_optional_sources_skip_when_dependency_missing(monkeypatch, module_name, cls_name, dep_modules):
    # Force the optional dependency to look uninstalled (a None entry in
    # sys.modules makes `import dep` raise ImportError) so fetch() takes the
    # skip path regardless of whether the package is actually installed — the
    # documented `pip install -r requirements.txt` setup installs both.
    import importlib
    import sys

    for dep in dep_modules:
        monkeypatch.setitem(sys.modules, dep, None)

    mod = importlib.import_module(f"job_search.sources.{module_name}")
    cls = getattr(mod, cls_name)
    assert cls().fetch() == []


def test_fetch_source_captures_exception():
    class Boom(base.BaseSource):
        name = "boom"

        def fetch(self, verbose=False):
            raise RuntimeError("kaboom")

    name, jobs, err = fetch_mod.fetch_source("boom", Boom, False)
    assert name == "boom"
    assert jobs == []
    assert isinstance(err, RuntimeError)


def test_fetch_jobs_runs_pipeline(monkeypatch):
    today = dt.date.today()

    class Fake(base.BaseSource):
        name = "fake"

        def fetch(self, verbose=False):
            return [
                Job(title="iOS Engineer", company="Acme", location="Berlin, Germany",
                    url="https://x/eu", description="Swift UIKit fully remote",
                    is_remote=True, date_posted=today),
                Job(title="QA Engineer", company="Acme", location="Berlin",
                    url="https://x/qa", description="ios swift", is_remote=True, date_posted=today),
            ]

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"fake": Fake})
    jobs = fetch_mod.fetch_jobs(source_names=["fake"])
    assert [j.title for j in jobs] == ["iOS Engineer"]


def test_fetch_jobs_empty_for_unknown_source(monkeypatch):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"fake": object})
    assert fetch_mod.fetch_jobs(source_names=["nonexistent"]) == []
