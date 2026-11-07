"""Sources area: remotive, remoteok and weworkremotely parsing and fetch health.

Every case feeds scripted HTTP responses to the engine at
``urllib.request.urlopen`` (so ``http_request``, ``read_capped`` and the
charset decoding really run), then calls the fetch stage's own
``_fetch_source_with_diagnostics`` and ``_source_health``.

Failure kinds are short stable strings derived from each failure the engine
records (first match wins; an unmapped failure stops the export):

    "HTTP <n>" recorded by the source            -> "http_status"
    urllib.error.HTTPError                       -> "http_status"
    urllib.error.URLError (not HTTPError)        -> "transport"
    json.JSONDecodeError                         -> "invalid_json"
    xml.etree.ElementTree.ParseError             -> "invalid_xml"
    AttributeError / TypeError / KeyError        -> "invalid_shape"
"""
import base64
import contextlib
import http.client
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from unittest import mock

from job_search.config import MAX_RESPONSE_BYTES, SearchConfig
from job_search.sources import base
from job_search.sources import fetch as fetch_mod
from job_search.sources.api_sources import RemoteOKSource, RemotiveSource
from job_search.sources.rss_sources import WeWorkRemotelySource

NOTES = [
    "Dates: remoteok epoch timestamps become dates in the local zone in the engine; Swift uses UTC "
    "(Decision 7). The exporter runs with TZ=UTC, so the goldens agree with Swift.",
    "Bodies: a response body is stored as text when its bytes are valid UTF-8, else as "
    "{\"base64\": <standard base64 of the bytes>}. A body over the 8 MiB cap is stored as "
    "{\"prefix\": <text>, \"repeat\": <text>, \"times\": <n>, \"suffix\": <text>}: the bytes are the UTF-8 "
    "of prefix + repeat * times + suffix. body_bytes is the full length; read_sizes are the n values "
    "the engine passed to read(n).",
    "Responses: a scripted status of 400 or more is raised as urllib.error.HTTPError, as the real "
    "urlopen does; such a response never reaches the source's status check. A response with "
    "\"error\": \"transport\" is raised as urllib.error.URLError.",
    "Requests: header names are as urllib stores them (str.capitalize(), e.g. \"User-agent\"); "
    "compare them case-insensitively.",
    "Search settings: remotive, remoteok and weworkremotely never read the search settings; their "
    "requests are fixed. Each case still carries the settings it was run with.",
    "Health: failure_detail is the engine's exception text (Python json and expat messages). Swift "
    "should match status, raw_job_count, attempt_count and failure_kinds, not failure_detail.",
    "Postings are the raw source output (before prefilter and dedupe): region is UNKNOWN and "
    "matched_skills empty.",
]

SEARCH = SearchConfig(search_terms=("iOS Engineer",), query_locations=("Remote",), results_per_query=15)

SOURCES = {
    "remotive": RemotiveSource,
    "remoteok": RemoteOKSource,
    "weworkremotely": WeWorkRemotelySource,
}

REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit=100"
REMOTEOK_URL = "https://remoteok.com/api"
WWR_PROGRAMMING = WeWorkRemotelySource.FEEDS[0]
WWR_DEVOPS = WeWorkRemotelySource.FEEDS[1]

JSON_TYPE = "application/json"
RSS_TYPE = "application/rss+xml; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"


class ScriptError(BaseException):
    """A broken script. BaseException, so the engine's `except Exception` cannot swallow it."""


# ── urlopen harness (enrich.py uses it too) ───────────────────────────────────
def body_bytes(body):
    """The bytes a stored body stands for (see NOTES)."""
    if isinstance(body, str):
        return body.encode("utf-8")
    if "base64" in body:
        return base64.b64decode(body["base64"])
    return (body["prefix"] + body["repeat"] * body["times"] + body["suffix"]).encode("utf-8")


class FakeResponse:
    def __init__(self, url, spec, reads):
        self.status = spec["status"]
        self.headers = http.client.HTTPMessage()
        for name, value in spec.get("headers", {}).items():
            self.headers[name] = value
        self._final_url = spec.get("final_url", url)
        self._data = body_bytes(spec.get("body", ""))
        self._pos = 0
        self._reads = reads

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._final_url

    def read(self, n=-1):
        self._reads.append(n)
        end = len(self._data) if n is None or n < 0 else self._pos + n
        chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        return chunk


class FakeNetwork:
    """Scripted urlopen: each URL answers its spec once, in order."""

    def __init__(self, responses):
        self.responses = {url: list(specs) for url, specs in responses.items()}
        self.requests = []
        self.reads = []

    def urlopen(self, req, timeout=None, context=None):
        url = req.full_url
        self.requests.append({
            "method": req.get_method(),
            "url": url,
            "headers": dict(req.header_items()),
            "timeout": timeout,
            "tls_verified": None if context is None else context.verify_mode == ssl.CERT_REQUIRED,
        })
        queue = self.responses.get(url)
        if not queue:
            raise ScriptError(f"no scripted response for {url}")
        spec = queue.pop(0)
        if spec.get("error") == "transport":
            raise urllib.error.URLError(spec["reason"])
        if spec["status"] >= 400:
            headers = http.client.HTTPMessage()
            for name, value in spec.get("headers", {}).items():
                headers[name] = value
            raise urllib.error.HTTPError(url, spec["status"], spec["reason"], headers, io.BytesIO(body_bytes(spec.get("body", ""))))
        return FakeResponse(url, spec, self.reads)

    @contextlib.contextmanager
    def patched(self):
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            yield

    def assert_drained(self):
        for url, queue in self.responses.items():
            if queue:
                raise ScriptError(f"unused scripted responses for {url}")


# ── failure kinds ─────────────────────────────────────────────────────────────
def _failure_kind(error):
    if isinstance(error, str) and error.startswith("HTTP "):
        return "http_status"
    if isinstance(error, urllib.error.HTTPError):
        return "http_status"
    if isinstance(error, urllib.error.URLError):
        return "transport"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(error, ET.ParseError):
        return "invalid_xml"
    if isinstance(error, (AttributeError, TypeError, KeyError)):
        return "invalid_shape"
    raise AssertionError(f"unmapped engine failure: {type(error).__name__}: {error}")


# ── inline responses of tests/test_sources.py ─────────────────────────────────
_TEST_REMOTIVE = {"jobs": [{
    "title": "iOS Engineer", "company_name": "Acme", "candidate_required_location": "Remote",
    "url": "https://x/1", "publication_date": "2024-03-15", "description": "d", "tags": ["swift"],
}]}

_TEST_REMOTEOK = [
    {"legal": "metadata row"},
    {"position": "iOS Dev", "company": "Acme", "location": "Remote",
     "url": "/remote-jobs/1", "epoch": 1609502400, "tags": ["swift"], "description": "d"},
]

_TEST_WWR = """<?xml version="1.0"?>
<rss><channel><item>
  <title>Acme: iOS Developer</title>
  <link>https://x/1</link>
  <description>desc</description>
  <pubDate>Mon, 15 Mar 2024 10:00:00 +0000</pubDate>
  <region>Europe</region>
  <country>Germany</country>
</item></channel></rss>"""

_WWR_SECOND = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Feed</title>
<item>
  <title>Example Labs: Site Reliability Engineer</title>
  <link> https://example.com/wwr/sre-1 </link>
  <description>&lt;p&gt;Run &amp;amp; scale things.&lt;/p&gt;</description>
  <pubDate>Sun, 01 Nov 2026 23:30:00 -0500</pubDate>
  <region>Anywhere in the World</region>
</item>
<item>
  <title>No company separator here</title>
  <link>https://example.com/wwr/plain</link>
  <pubDate>not a date</pubDate>
</item>
</channel></rss>"""


def _json(payload):
    return json.dumps(payload, ensure_ascii=False)


def _ok(body, content_type=JSON_TYPE):
    return {"status": 200, "headers": {"Content-Type": content_type}, "body": body}


def _http_error(status, reason, body="<html><body><h1>Service Unavailable</h1></body></html>"):
    return {"status": status, "reason": reason, "headers": {"Content-Type": HTML_TYPE}, "body": body}


def _b64(raw):
    return {"base64": base64.b64encode(raw).decode("ascii")}


_HTML_PAGE = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Just a moment...</title></head><body><p>Checking your browser.<br></p></body></html>'
# Well-formed XML, so ET.fromstring accepts it as a feed with no items.
_XHTML_PAGE = "<html><head><title>Just a moment...</title></head><body><p>Checking your browser.</p></body></html>"

_PAD = MAX_RESPONSE_BYTES  # bytes of filler: more than the cap whatever the prefix

# (name, source test or "synthetic", source, {url: [response spec, ...]})
_POSTING_CASES = [
    # inline responses of tests/test_sources.py
    ("remotive_test_inline", "test_remotive", "remotive", {REMOTIVE_URL: [_ok(_json(_TEST_REMOTIVE))]}),
    ("remoteok_test_inline", "test_remoteok_skips_leading_metadata", "remoteok", {REMOTEOK_URL: [_ok(_json(_TEST_REMOTEOK))]}),
    ("weworkremotely_test_inline", "test_weworkremotely", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_TEST_WWR, RSS_TYPE)], WWR_DEVOPS: [_ok(_TEST_WWR, RSS_TYPE)],
    }),
    # synthetic: normal shapes
    ("remotive_fields", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_json({"jobs": [
        {"title": " Senior Swift Engineer ", "company_name": "Acme Corp", "candidate_required_location": "",
         "url": "https://example.com/remotive/1", "publication_date": "2026-10-30T23:15:00",
         "description": "<p>Build apps.</p>", "category": "Software Development", "job_type": "full_time",
         "tags": ["swift", "", "ios"]},
        {"title": "Backend Engineer", "company_name": "Example Labs", "candidate_required_location": "Europe",
         "url": "https://example.com/remotive/2", "publication_date": "2026-10-31T01:00:00Z",
         "description": None, "category": None, "job_type": None, "tags": None},
    ]}))]}),
    ("remoteok_fields", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_json([
        {"legal": "metadata row"},
        {"position": "iOS Engineer", "company": "Acme Corp", "location": "",
         "url": "https://example.com/remoteok/1", "epoch": 1609459500, "tags": ["swift", "ios"], "description": "<b>Swift</b>"},
        {"position": "Platform Engineer", "company": "Example Labs", "location": "Worldwide",
         "url": "/remote-jobs/2", "epoch": "", "date": "2026-10-29T08:00:00+02:00", "tags": [], "description": ""},
        "not an object",
        {"position": "No date", "company": "Example Labs", "url": "", "tags": None},
    ]))]}),
    ("weworkremotely_fields", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_TEST_WWR, RSS_TYPE)], WWR_DEVOPS: [_ok(_WWR_SECOND, RSS_TYPE)],
    }),
]

_HEALTH_CASES = [
    ("remotive_empty_body", "synthetic", "remotive", {REMOTIVE_URL: [_ok("")]}),
    ("remoteok_empty_body", "synthetic", "remoteok", {REMOTEOK_URL: [_ok("")]}),
    ("weworkremotely_empty_body", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok("", RSS_TYPE)], WWR_DEVOPS: [_ok("", RSS_TYPE)],
    }),
    ("remotive_html_page", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_HTML_PAGE, HTML_TYPE)]}),
    ("remoteok_html_page", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_HTML_PAGE, HTML_TYPE)]}),
    ("weworkremotely_html_page", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_HTML_PAGE, HTML_TYPE)], WWR_DEVOPS: [_ok(_HTML_PAGE, HTML_TYPE)],
    }),
    ("weworkremotely_xhtml_page", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_XHTML_PAGE, HTML_TYPE)], WWR_DEVOPS: [_ok(_XHTML_PAGE, HTML_TYPE)],
    }),
    ("remotive_http_503", "synthetic", "remotive", {REMOTIVE_URL: [_http_error(503, "Service Unavailable")]}),
    ("remoteok_http_403", "synthetic", "remoteok", {REMOTEOK_URL: [_http_error(403, "Forbidden")]}),
    ("weworkremotely_http_503_one_feed", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_http_error(503, "Service Unavailable")], WWR_DEVOPS: [_ok(_TEST_WWR, RSS_TYPE)],
    }),
    ("remotive_transport_error", "synthetic", "remotive", {REMOTIVE_URL: [{"error": "transport", "reason": "connection refused"}]}),
    ("weworkremotely_transport_both", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [{"error": "transport", "reason": "connection refused"}],
        WWR_DEVOPS: [{"error": "transport", "reason": "connection refused"}],
    }),
    ("remotive_status_204_empty", "synthetic", "remotive", {REMOTIVE_URL: [{"status": 204, "headers": {}, "body": ""}]}),
    ("remotive_status_203_json", "synthetic", "remotive", {REMOTIVE_URL: [
        {"status": 203, "headers": {"Content-Type": JSON_TYPE}, "body": _json(_TEST_REMOTIVE)},
    ]}),
    ("remoteok_status_203_json", "synthetic", "remoteok", {REMOTEOK_URL: [
        {"status": 203, "headers": {"Content-Type": JSON_TYPE}, "body": _json(_TEST_REMOTEOK)},
    ]}),
    ("weworkremotely_status_203", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [{"status": 203, "headers": {"Content-Type": RSS_TYPE}, "body": _TEST_WWR}],
        WWR_DEVOPS: [_ok(_TEST_WWR, RSS_TYPE)],
    }),
    ("remotive_invalid_utf8", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_b64(
        b'{"jobs":[{"title":"iOS \xff\xfe Engineer","company_name":"Acme Corp","url":"https://example.com/remotive/u"}]}'
    ))]}),
    ("remoteok_invalid_utf8", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_b64(
        b'[{"legal":"x"},{"position":"Dev \xc3","company":"Acme Corp","url":"https://example.com/remoteok/u"}]'
    ))]}),
    ("weworkremotely_invalid_utf8", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_b64(
            b'<?xml version="1.0"?><rss><channel><item><title>Acme Corp: Caf\xe9 Engineer</title>'
            b'<link>https://example.com/wwr/u</link></item></channel></rss>'
        ), RSS_TYPE)],
        WWR_DEVOPS: [_ok(_TEST_WWR, RSS_TYPE)],
    }),
    ("remotive_latin1_charset", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_b64(
        '{"jobs":[{"title":"Café Engineer","company_name":"Acme Corp","url":"https://example.com/remotive/l"}]}'.encode("latin-1")
    ), "application/json; Charset=\"ISO-8859-1\"")]}),
    ("remotive_missing_fields", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_json({"jobs": [{}]}))]}),
    ("remotive_no_jobs_key", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_json({"items": []}))]}),
    ("remotive_top_level_list", "synthetic", "remotive", {REMOTIVE_URL: [_ok(_json([]))]}),
    ("remoteok_missing_fields", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_json([{"legal": "x"}, {}]))]}),
    ("remoteok_metadata_only", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_json([{"legal": "metadata row"}]))]}),
    ("remoteok_top_level_object", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(_json({"legal": "x"}))]}),
    ("weworkremotely_missing_fields", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok('<?xml version="1.0"?><rss><channel><item></item></channel></rss>', RSS_TYPE)],
        WWR_DEVOPS: [_ok('<?xml version="1.0"?><rss><channel></channel></rss>', RSS_TYPE)],
    }),
    ("weworkremotely_malformed_one_feed", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(_TEST_WWR, RSS_TYPE)],
        WWR_DEVOPS: [_ok('<?xml version="1.0"?><rss><channel><item><title>Broken</item></channel></rss>', RSS_TYPE)],
    }),
    ("remotive_over_cap_padded", "synthetic", "remotive", {REMOTIVE_URL: [_ok(
        {"prefix": _json(_TEST_REMOTIVE), "repeat": " ", "times": _PAD, "suffix": ""}
    )]}),
    ("remoteok_over_cap_truncated", "synthetic", "remoteok", {REMOTEOK_URL: [_ok(
        {"prefix": '[{"legal":"x"},', "repeat": '{"position":"Filler","company":"Acme Corp"},',
         "times": _PAD // 40, "suffix": '{"position":"Last"}]'}
    )]}),
    ("weworkremotely_over_cap_one_feed", "synthetic", "weworkremotely", {
        WWR_PROGRAMMING: [_ok(
            {"prefix": '<?xml version="1.0"?><rss><channel>', "repeat": "<item><title>Acme Corp: Filler</title></item>",
             "times": _PAD // 40, "suffix": "</channel></rss>"}, RSS_TYPE,
        )],
        WWR_DEVOPS: [_ok(_TEST_WWR, RSS_TYPE)],
    }),
]


def _run_case(name, source_test, source_name, responses):
    net = FakeNetwork(responses)
    kinds = []
    original = base.BaseSource._attempt_failed

    def record_failure(self, error):
        kinds.append(_failure_kind(error))
        return original(self, error)

    with net.patched(), mock.patch.object(base.BaseSource, "_attempt_failed", record_failure):
        result_name, jobs, error, source = fetch_mod._fetch_source_with_diagnostics(
            source_name, SOURCES[source_name], False, None, SEARCH, None, None,
        )
    net.assert_drained()
    if error is not None:
        kinds.append(_failure_kind(error))
    health = fetch_mod._source_health(result_name, jobs, error, source)
    stored = {
        url: [dict(spec, body_bytes=len(body_bytes(spec.get("body", "")))) if "status" in spec else spec for spec in specs]
        for url, specs in responses.items()
    }
    return {
        "name": name,
        "source_test": source_test,
        "source": source_name,
        "search": {
            "search_terms": list(SEARCH.search_terms),
            "query_locations": list(SEARCH.query_locations),
            "results_per_query": SEARCH.results_per_query,
        },
        "responses": stored,
        "requests": net.requests,
        "read_sizes": net.reads,
        "postings": [job.to_dict() for job in jobs],
        "health": {
            "source": health.source,
            "status": health.status.value,
            "raw_job_count": health.raw_job_count,
            "attempt_count": health.attempt_count,
            "failure_detail": health.failure_detail,
            "failure_kinds": kinds,
        },
    }


def _check_utc():
    # parse_epoch_date uses the local zone; see NOTES.
    if os.environ.get("TZ") != "UTC" or time.localtime(1609459500).tm_gmtoff != 0:
        raise SystemExit("run with TZ=UTC: remoteok epoch dates depend on the local zone")


def export():
    _check_utc()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return {
            "postings.json": [_run_case(*case) for case in _POSTING_CASES],
            "health.json": [_run_case(*case) for case in _HEALTH_CASES],
        }
