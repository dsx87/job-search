"""Characterization tests for each source's parse path.

Network is replaced by monkeypatching each source module's http_json /
http_request binding, so these run fully offline. Optional sources
(jobspy/linkedin/secrettelaviv) are verified to skip gracefully when their
dependency is absent.
"""
import datetime as dt
from collections import OrderedDict

import pytest

# --- modules under test (repoint on migration) ---
from job_search.models import Job
from job_search.sources import base
from job_search.sources import api_sources, html_sources, rss_sources
from job_search.sources import fetch as fetch_mod
from job_search.sources import linkedin_guest
from job_search.sources.health import SourceStatus
from job_search.state.seen_jobs import normalize_url
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


# ── Default-on/off source selection (Part 2) ──────────────────────────────────
def _selection_registry():
    """Ordered fake registry: two default-on sources and one default-off."""
    class A(base.BaseSource):
        name = "a"

    class B(base.BaseSource):
        name = "b"

    class Off(base.BaseSource):
        name = "off"
        default_enabled = False

    return OrderedDict([("a", A), ("b", B), ("off", Off)])


def test_default_source_names_excludes_default_off(monkeypatch):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", _selection_registry())
    assert fetch_mod.default_source_names() == ["a", "b"]
    # Empty enable/disable reproduces the default-on set.
    assert fetch_mod.select_sources() == ["a", "b"]


def test_default_source_names_treats_bare_class_as_on(monkeypatch):
    # A class with no default_enabled attr (getattr fallback) counts as on.
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", OrderedDict([("bare", object)]))
    assert fetch_mod.default_source_names() == ["bare"]


def test_select_sources_enable_resurrects_default_off(monkeypatch):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", _selection_registry())
    # Registry order preserved: 'off' comes last.
    assert fetch_mod.select_sources(enable=("off",)) == ["a", "b", "off"]


def test_select_sources_disable_removes_default_on(monkeypatch):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", _selection_registry())
    assert fetch_mod.select_sources(disable=("a",)) == ["b"]


def test_select_sources_enable_wins_over_disable(monkeypatch):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", _selection_registry())
    # 'a' in both lists → enable wins, stays selected.
    assert fetch_mod.select_sources(enable=("a",), disable=("a",)) == ["a", "b"]


def test_select_sources_warns_on_unknown(monkeypatch, capsys):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", _selection_registry())
    result = fetch_mod.select_sources(enable=("bogus",), disable=("nope",))
    assert result == ["a", "b"]  # unknowns ignored, defaults unchanged
    err = capsys.readouterr().err
    assert "Ignoring unknown sources" in err
    assert "bogus" in err and "nope" in err


def test_fetch_jobs_none_uses_default_on_set(monkeypatch):
    today = dt.date.today()

    class On(base.BaseSource):
        name = "on"

        def fetch(self, verbose=False):
            return [Job(title="iOS Engineer", company="Acme", location="Berlin, Germany",
                        url="https://x/on", description="Swift UIKit fully remote",
                        is_remote=True, date_posted=today)]

    class Off(base.BaseSource):
        name = "off"
        default_enabled = False

        def fetch(self, verbose=False):
            raise AssertionError("default-off source must not run when source_names is None")

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", OrderedDict([("on", On), ("off", Off)]))
    jobs = fetch_mod.fetch_jobs(source_names=None)
    assert [j.company for j in jobs] == ["Acme"]


def test_fetch_jobs_abandons_source_exceeding_budget(monkeypatch, capsys):
    """A source slower than the fetch budget is abandoned; the run proceeds with
    whatever the other sources returned, instead of hanging on the slow one."""
    import time

    today = dt.date.today()

    def _ios_job(company, url):
        return Job(title="iOS Engineer", company=company, location="Berlin, Germany",
                   url=url, description="Swift UIKit fully remote", is_remote=True,
                   date_posted=today)

    class Fast(base.BaseSource):
        name = "fast"

        def fetch(self, verbose=False):
            return [_ios_job("FastCo", "https://x/fast")]

    class Slow(base.BaseSource):
        name = "slow"

        def fetch(self, verbose=False):
            time.sleep(10)  # far longer than the tiny budget below
            return [_ios_job("SlowCo", "https://x/slow")]

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"fast": Fast, "slow": Slow})

    start = time.monotonic()
    jobs = fetch_mod.fetch_jobs(source_names=["fast", "slow"], budget_seconds=0.5, verbose=True)
    elapsed = time.monotonic() - start

    # Returned promptly — did not wait for the slow source's 10s sleep.
    assert elapsed < 5
    # Kept the fast source's job; abandoned the slow one.
    assert [j.company for j in jobs] == ["FastCo"]
    # The truncation is surfaced (names the abandoned source) rather than silent.
    assert "slow" in capsys.readouterr().err


def test_fetch_jobs_with_health_reports_empty_success(monkeypatch):
    class Empty(base.BaseSource):
        name = "empty"

        def fetch(self, verbose=False):
            self._attempt_succeeded()
            return []

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"empty": Empty})
    report = fetch_mod.fetch_jobs_with_health(source_names=["empty"])

    assert report.jobs == ()
    assert report.has_usable_source is True
    assert report.outcomes[0].status is SourceStatus.SUCCESS
    assert report.outcomes[0].raw_job_count == 0
    assert report.outcomes[0].attempt_count == 1


def test_fetch_threads_configured_seen_file_to_source_instances(monkeypatch):
    observed = []

    class Empty(base.BaseSource):
        name = "empty"

        def fetch(self, verbose=False):
            observed.append(self.seen_jobs_file)
            return []

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"empty": Empty})

    fetch_mod.fetch_jobs_with_health(
        source_names=["empty"], seen_jobs_file="state/custom.json"
    )

    assert observed == ["state/custom.json"]


def test_fetch_jobs_with_health_reports_failed_and_partial_sources_in_order(monkeypatch):
    today = dt.date.today()

    class Failed(base.BaseSource):
        name = "failed"

        def fetch(self, verbose=False):
            self._attempt_failed(RuntimeError("service unavailable"))
            return []

    class Partial(base.BaseSource):
        name = "partial"

        def fetch(self, verbose=False):
            self._attempt_succeeded()
            self._attempt_failed(RuntimeError("second page failed"))
            return [Job(title="iOS Engineer", company="Acme", location="Berlin, Germany",
                        url="https://x/partial", description="Swift UIKit fully remote",
                        is_remote=True, date_posted=today, source=self.name)]

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", OrderedDict([
        ("failed", Failed), ("partial", Partial),
    ]))
    report = fetch_mod.fetch_jobs_with_health(source_names=["failed", "partial"])

    assert [outcome.source for outcome in report.outcomes] == ["failed", "partial"]
    assert [outcome.status for outcome in report.outcomes] == [
        SourceStatus.FAILED, SourceStatus.PARTIAL,
    ]
    assert report.outcomes[1].attempt_count == 2
    assert report.outcomes[1].failure_detail == "second page failed"
    assert [job.company for job in report.jobs] == ["Acme"]
    assert report.has_usable_source is True


def test_fetch_jobs_with_health_reports_skip_exception_and_timeout(monkeypatch):
    import time

    class Skipped(base.BaseSource):
        name = "skipped"

        def fetch(self, verbose=False):
            self._skip("optional dependency missing")
            return []

    class Boom(base.BaseSource):
        name = "boom"

        def fetch(self, verbose=False):
            raise RuntimeError("kaboom")

    class Slow(base.BaseSource):
        name = "slow"

        def fetch(self, verbose=False):
            time.sleep(1)
            return []

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", OrderedDict([
        ("skipped", Skipped), ("boom", Boom), ("slow", Slow),
    ]))
    report = fetch_mod.fetch_jobs_with_health(
        source_names=["skipped", "boom", "slow"], budget_seconds=0.05,
    )

    assert [outcome.status for outcome in report.outcomes] == [
        SourceStatus.SKIPPED, SourceStatus.FAILED, SourceStatus.TIMED_OUT,
    ]
    assert report.outcomes[0].failure_detail == "optional dependency missing"
    assert report.outcomes[1].failure_detail == "kaboom"
    assert report.has_usable_source is False
    assert report.incomplete_sources == ("skipped", "boom", "slow")


def test_fetch_jobs_compatibility_wrapper_returns_list(monkeypatch):
    class Empty(base.BaseSource):
        name = "empty"

        def fetch(self, verbose=False):
            return []

    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"empty": Empty})
    assert fetch_mod.fetch_jobs(source_names=["empty"]) == []
    assert isinstance(fetch_mod.fetch_jobs(source_names=["empty"]), list)


def test_linkedin_guest_budget_expiration_before_first_request_is_timed_out(monkeypatch):
    source = linkedin_guest.LinkedInGuestSource()
    times = iter((0.0, 2.0))
    monkeypatch.setenv("LINKEDIN_BUDGET_SECONDS", "1")
    monkeypatch.setattr(linkedin_guest.time, "monotonic", lambda: next(times))

    jobs = source.fetch()
    outcome = fetch_mod._source_health(source.name, jobs, None, source)

    assert outcome.status is SourceStatus.TIMED_OUT
    assert outcome.attempt_count == 0
    assert "budget" in outcome.failure_detail


def test_source_budget_expiration_after_success_is_partial():
    source = base.BaseSource()
    source._attempt_succeeded()
    source._timed_out("source budget reached")

    outcome = fetch_mod._source_health("partial", [Job(title="iOS")], None, source)

    assert outcome.status is SourceStatus.PARTIAL
    assert outcome.raw_job_count == 1


def test_run_scraper_keeps_json_stdout_pure_and_reports_health(monkeypatch, capsys):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"empty": object})
    report = fetch_mod.FetchReport((), (
        fetch_mod.SourceHealth("empty", SourceStatus.SUCCESS, 0, 1),
    ))
    monkeypatch.setattr(fetch_mod, "fetch_jobs_with_health", lambda **_kwargs: report)

    assert fetch_mod.run_scraper(source_names=["empty"], as_json=True) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"
    assert "empty: success" in captured.err


def test_run_scraper_returns_nonzero_when_every_source_is_unusable(monkeypatch, capsys):
    monkeypatch.setattr(fetch_mod, "ALL_SOURCES", {"down": object})
    report = fetch_mod.FetchReport((), (
        fetch_mod.SourceHealth("down", SourceStatus.FAILED, failure_detail="offline"),
    ))
    monkeypatch.setattr(fetch_mod, "fetch_jobs_with_health", lambda **_kwargs: report)

    assert fetch_mod.run_scraper(source_names=["down"], as_json=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No usable job source" in captured.err


# ── linkedin-guest offline parse (Part 3) ─────────────────────────────────────
# Two cards; job 111 appears twice (within-run dedup). Whitespace and an HTML
# entity in the first title exercise the _clean() path.
_LG_SEARCH_HTML = """
<ul>
<li>
<div class="base-card relative" data-entity-urn="urn:li:jobPosting:111">
  <h3 class="base-search-card__title">
    iOS   Engineer &amp; Developer
  </h3>
  <h4 class="base-search-card__subtitle">
    <a class="hidden-nested-link" href="/company/acme">Acme Corp</a>
  </h4>
  <span class="job-search-card__location">
    Berlin, Germany
  </span>
  <time class="job-search-card__listdate" datetime="2026-07-10">1 week ago</time>
</div>
</li>
<li>
<div class="base-card relative" data-entity-urn="urn:li:jobPosting:222">
  <h3 class="base-search-card__title">Backend Engineer</h3>
  <h4 class="base-search-card__subtitle">
    <a class="hidden-nested-link" href="/company/globex">Globex</a>
  </h4>
  <span class="job-search-card__location">Toronto, Canada</span>
  <time datetime="2026-07-09">2 weeks ago</time>
</div>
</li>
<li>
<div class="base-card relative" data-entity-urn="urn:li:jobPosting:111">
  <h3 class="base-search-card__title">iOS Engineer &amp; Developer</h3>
  <h4 class="base-search-card__subtitle">
    <a class="hidden-nested-link" href="/company/acme">Acme Corp</a>
  </h4>
  <span class="job-search-card__location">Berlin, Germany</span>
  <time datetime="2026-07-10">1 week ago</time>
</div>
</li>
</ul>
"""

_LG_DETAIL_HTML = """
<section>
<div class="show-more-less-html__markup relative overflow-hidden">
  <strong>We are hiring an iOS engineer.</strong> Swift, UIKit, fully remote.
</div>
</section>
"""


def _lg_fake_http(calls):
    """URL-dispatching fake for linkedin_guest.http_request → (status, text)."""
    def fake(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        if "seeMoreJobPostings" in url:
            return 200, _LG_SEARCH_HTML
        return 200, _LG_DETAIL_HTML
    return fake


def _lg_patch(monkeypatch, calls, seen):
    monkeypatch.setattr(linkedin_guest, "QUERIES", ["iOS"])
    monkeypatch.setattr(linkedin_guest, "LOCATIONS", [("European Union", 10)])
    monkeypatch.setattr(linkedin_guest, "POLITE_DELAY", 0)
    monkeypatch.setattr(linkedin_guest, "http_request", _lg_fake_http(calls))
    monkeypatch.setattr("job_search.state.seen_jobs.load_seen_jobs", lambda: seen)


def test_linkedin_guest_parses_canonicalizes_and_dedupes(monkeypatch):
    calls = []
    _lg_patch(monkeypatch, calls, set())  # nothing seen
    jobs = linkedin_guest.LinkedInGuestSource().fetch(verbose=False)

    # Within-run dedup: job 111 appears twice in the page but yields one Job.
    # URLs are jobspy's canonical .../jobs/view/<id> form.
    assert [j.url for j in jobs] == [
        "https://www.linkedin.com/jobs/view/111",
        "https://www.linkedin.com/jobs/view/222",
    ]
    ios = jobs[0]
    assert ios.title == "iOS Engineer & Developer"  # whitespace collapsed, &amp; unescaped
    assert ios.company == "Acme Corp"
    assert ios.location == "Berlin, Germany"
    assert ios.source == "linkedin-guest"
    assert ios.date_posted == dt.date(2026, 7, 10)
    assert ios.description == "We are hiring an iOS engineer. Swift, UIKit, fully remote."


def test_linkedin_guest_skips_description_for_seen_job(monkeypatch):
    calls = []
    seen = {normalize_url("https://www.linkedin.com/jobs/view/111")}
    _lg_patch(monkeypatch, calls, seen)
    jobs = linkedin_guest.LinkedInGuestSource().fetch(verbose=False)
    by_url = {j.url: j for j in jobs}

    # Seen job is still returned (downstream dedup drops it) but its expensive
    # description request is skipped.
    assert by_url["https://www.linkedin.com/jobs/view/111"].description == ""
    assert not any("jobPosting/111" in u for u in calls)
    # The genuinely-new job still fetches its description.
    assert by_url["https://www.linkedin.com/jobs/view/222"].description == (
        "We are hiring an iOS engineer. Swift, UIKit, fully remote."
    )
    assert any("jobPosting/222" in u for u in calls)


def test_linkedin_guest_recognizes_seen_fallback_alias(monkeypatch):
    calls = []
    seen = {"ios engineer & developer|acme corp|berlin, germany"}
    _lg_patch(monkeypatch, calls, seen)

    jobs = linkedin_guest.LinkedInGuestSource().fetch(verbose=False)

    by_url = {job.url: job for job in jobs}
    assert by_url["https://www.linkedin.com/jobs/view/111"].description == ""
    assert not any("jobPosting/111" in url for url in calls)
