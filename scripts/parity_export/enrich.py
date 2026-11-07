"""Enrich area: ``ensure_job_description`` with the real ``fetch_job_text_from_url``.

Pages are scripted at ``urllib.request.urlopen`` (the harness in ``sources.py``),
so the final-URL rule, charset decoding, ``read_capped`` and the
script/style/head/noscript strip all run in the engine.
"""
import base64
import contextlib
import io

from job_search.config import MIN_JOB_TEXT_LEN
from job_search.models import Job
from job_search.pipeline import stages

from .sources import FakeNetwork, body_bytes

NOTES = [
    "Accept-Language: the engine sends \"en-US,en;q=0.9,he;q=0.8\" (recorded in requests). Swift sends "
    "\"en-US,en;q=0.9\" (Decision 13); this is an intended difference.",
    "Bodies: a page body is stored as text when its bytes are valid UTF-8, else as "
    "{\"base64\": <standard base64 of the bytes>}; a body over the 8 MiB cap as "
    "{\"prefix\", \"repeat\", \"times\", \"suffix\"} (bytes = UTF-8 of prefix + repeat * times + suffix).",
    "Pages: a scripted status of 400 or more is raised as urllib.error.HTTPError and "
    "\"error\": \"transport\" as urllib.error.URLError, as the real urlopen does. final_url is what "
    "geturl() returns (the URL after redirects); it defaults to the requested URL.",
    "Requests: header names are as urllib stores them (str.capitalize(), e.g. \"User-agent\"); compare "
    "them case-insensitively. tls_verified is null: the enricher passes no SSL context (urllib's default, "
    "which verifies).",
    "min_length is the engine default MIN_JOB_TEXT_LEN unless the case sets it.",
]

HTML_TYPE = "text/html; charset=utf-8"

_PROSE = (
    "We are hiring a Senior Swift Engineer to build our native iOS apps. You will own features end to end, "
    "work with design and backend teams, write tests, review code and help us ship a fast, reliable product "
    "to many users every week."
)
_PAGE = f"<html><body><h1>Senior Swift Engineer</h1><p>{_PROSE}</p></body></html>"
_BROWSE = (
    "Browse all jobs at Acme Corp. Engineering, design, sales and support teams are growing in every office. "
    "Pick a team, read about the work, filter by location and apply in minutes. New openings are added every "
    "week, so check back often for more roles."
)


def _page(body, content_type=HTML_TYPE, status=200, final_url=None):
    spec = {"status": status, "headers": {"Content-Type": content_type} if content_type else {}, "body": body}
    if final_url is not None:
        spec["final_url"] = final_url
    return spec


def _job(url="https://example.com/jobs/123", description="tiny", title="Senior Swift Engineer"):
    return {
        "title": title, "company": "Acme Corp", "location": "Remote", "url": url,
        "source": "remotive", "date_posted": "2026-10-30", "description": description, "is_remote": True,
    }


_LATIN1_BODY = {"base64": base64.b64encode(f"<p>Café team. {_PROSE}</p>".encode("latin-1")).decode("ascii")}

# (name, source test or "synthetic", job fields, input kind, page spec or None, min_length or None)
_CASES = [
    ("short_description_page_wins", "synthetic", _job(), "job", _page(_PAGE), None),
    ("page_shorter_than_description", "synthetic", _job(description="Swift role in a small team. " * 5),
     "job", _page("<p>Short page about the role.</p>"), None),
    ("page_longer_but_under_minimum", "synthetic", _job(description="Swift role."),
     "job", _page("<p>A somewhat longer page that is still under the minimum length.</p>"), None),
    ("long_description_no_fetch", "synthetic", _job(description=_PROSE + " " + _PROSE), "job", None, None),
    ("long_html_description_cleaned_no_fetch", "synthetic",
     _job(description=f"<div><p>{_PROSE}</p>\n<ul><li>Swift &amp; SwiftUI</li></ul></div>"), "job", None, None),
    ("plain_description_angle_brackets_kept", "synthetic",
     _job(description=_PROSE + " Uses Map<String, Int> and <https://apply.example.com>."), "job", None, None),
    ("non_http_url_no_fetch", "synthetic", _job(url="mailto:jobs@example.com"), "job", None, None),
    ("empty_url_no_fetch", "synthetic", _job(url=""), "job", None, None),
    ("host_only_url_no_netloc", "synthetic", _job(url="https:///jobs/1"), "job", None, None),
    ("redirect_same_path_other_host", "synthetic", _job(), "job",
     _page(_PAGE, final_url="https://jobs.example.org/Jobs/123/"), None),
    ("redirect_to_jobs", "synthetic", _job(), "job", _page(_PAGE, final_url="https://example.com/jobs"), None),
    ("redirect_to_careers_mixed_case_slash", "synthetic", _job(), "job",
     _page(_PAGE, final_url="https://example.com/Careers/"), None),
    ("redirect_to_openings", "synthetic", _job(), "job", _page(_PAGE, final_url="https://example.com/openings"), None),
    ("redirect_to_positions_other_host", "synthetic", _job(), "job",
     _page(_PAGE, final_url="https://careers.example.org/positions"), None),
    ("redirect_to_jobs_subpath", "synthetic", _job(), "job", _page(_PAGE, final_url="https://example.com/jobs/456"), None),
    ("same_url_careers_not_a_redirect", "synthetic", _job(url="https://example.com/careers"), "job",
     _page(_PAGE, final_url="https://example.com/careers/"), None),
    ("browse_all_jobs_without_title", "synthetic", _job(), "job", _page(f"<p>{_BROWSE}</p>"), None),
    ("browse_all_jobs_with_title", "synthetic", _job(), "job",
     _page(f"<p>{_BROWSE} Open now: Senior SWIFT Engineer.</p>"), None),
    ("browse_all_jobs_empty_title", "synthetic", _job(title=""), "job", _page(f"<p>{_BROWSE}</p>"), None),
    ("view_open_positions_without_title", "synthetic", _job(), "job",
     _page("<p>View open positions. " + _BROWSE + "</p>"), None),
    ("browse_text_not_at_start", "synthetic", _job(), "job", _page(f"<p>Welcome. {_BROWSE}</p>"), None),
    ("fetch_transport_error", "synthetic", _job(), "job", {"error": "transport", "reason": "connection refused"}, None),
    ("fetch_http_404", "synthetic", _job(), "job",
     {"status": 404, "reason": "Not Found", "headers": {"Content-Type": HTML_TYPE}, "body": "<h1>Not found</h1>"}, None),
    ("head_script_style_noscript_stripped", "synthetic", _job(), "job", _page(
        "<html><HEAD><title>Senior Swift Engineer | Acme Corp</title><style>p { color: red; }</style></HEAD>"
        f"<body><script type=\"text/javascript\">var tracking = 'x';</script><noscript>Enable JS</noscript>"
        f"<p>{_PROSE}</p><Script>\nwindow.x = 1;\n</SCRIPT></body></html>"
    ), None),
    ("plain_text_page", "synthetic", _job(), "job", _page(_PROSE + "\n\n  Apply  today. ", "text/plain"), None),
    ("entities_and_nbsp", "synthetic", _job(), "job",
     _page(f"<p>{_PROSE}&nbsp;Salary&nbsp;&#8364;90k &amp; equity&#x21;</p>"), None),
    ("latin1_charset_page", "synthetic", _job(), "job",
     _page(_LATIN1_BODY, "text/html; charset=ISO-8859-1"), None),
    ("no_charset_invalid_utf8", "synthetic", _job(), "job",
     _page(_LATIN1_BODY, "text/html"), None),
    ("no_content_type", "synthetic", _job(), "job", _page(_PAGE, None), None),
    ("over_cap_page", "synthetic", _job(), "job",
     _page({"prefix": f"<p>{_PROSE}</p>", "repeat": "<br>\n", "times": 2 * 1024 * 1024, "suffix": "<p>END</p>"}), None),
    ("legacy_mapping_fetcher", "test_description_enrichment_updates_legacy_mapping",
     {"url": "https://x/job", "description": "tiny"}, "mapping", "fetcher:complete text", 10),
    ("legacy_mapping_page", "test_description_enrichment_updates_legacy_mapping",
     {"url": "https://x/job", "description": "tiny"}, "mapping", _page("complete text", "text/plain"), 10),
]


def _run_case(name, source_test, fields, kind, page, min_length):
    job = dict(fields) if kind == "mapping" else Job.from_dict(fields)
    before = Job.from_dict(fields).to_dict()
    kwargs = {} if min_length is None else {"min_length": min_length}
    fetcher_text = None
    if isinstance(page, str):
        fetcher_text = page[len("fetcher:"):]
        kwargs["fetcher"] = lambda _url: fetcher_text
        page = None
    net = FakeNetwork({} if page is None else {fields["url"]: [page]})
    with net.patched():
        kept = stages.ensure_job_description(job, **kwargs)
    net.assert_drained()
    after = (Job.from_dict(job) if kind == "mapping" else job).to_dict()
    stored_page = None if page is None else (
        page if "status" not in page else dict(page, body_bytes=len(body_bytes(page.get("body", ""))))
    )
    case = {
        "name": name,
        "source_test": source_test,
        "input": {"kind": kind, "job": fields},
        "min_length": MIN_JOB_TEXT_LEN if min_length is None else min_length,
        "page": stored_page,
        "fetcher_returns": fetcher_text,
        "requests": net.requests,
        "read_sizes": net.reads,
        "result": {
            "kept": kept,
            "description": after["description"],
            "changed_fields": sorted(key for key in after if after[key] != before[key]),
        },
    }
    if kind == "mapping":
        case["result"]["mapping"] = job
    return case


def export():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return {"descriptions.json": [_run_case(*case) for case in _CASES]}
