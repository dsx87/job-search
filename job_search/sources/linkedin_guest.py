"""Native LinkedIn source (stdlib-only), default-off.

Reproduces the LinkedIn coverage that ``python-jobspy`` gives us (the
``linkedin-global`` and ``linkedin-israel`` sources) by hitting LinkedIn's
public *guest* job-search endpoints directly with ``urllib`` — the same
endpoints jobspy scrapes under the hood. Because it uses nothing but the
standard library, it runs on the ARMv6 Raspberry Pi, where jobspy cannot even
import (its Indeed scraper pulls in ``tls_client``, whose Go shared library has
no 32-bit ARM build).

Registered ``default_enabled=False``: it stays out of the default fetch set (CI
keeps the jobspy-backed LinkedIn sources) and only runs when explicitly enabled,
e.g. ``SOURCES_ENABLE=linkedin-guest`` on the Pi or ``--sources linkedin-guest``.

Endpoints (no auth, plain HTTP):
  * search:      /jobs-guest/jobs/api/seeMoreJobPostings/search  -> HTML job cards
  * description: /jobs-guest/jobs/api/jobPosting/<id>            -> HTML detail page

Job URLs are emitted in jobspy's canonical form ``.../jobs/view/<id>`` (lowercased,
no trailing slash) so they dedup exactly against the LinkedIn entries already in
seen_jobs.json.
"""
import html as _html
import os
import re
import time
import urllib.error

from ..dates import parse_iso_date
from ..http import http_request
from ..identity import job_identity_keys
from ..models import Job
from .base import BaseSource, register

NAME = "linkedin-guest"
DESCRIPTION = (
    "LinkedIn iOS/macOS jobs (EU, Canada, Israel) via LinkedIn's public guest API. "
    "Stdlib-only replacement for the jobspy-backed LinkedIn sources; runs on ARMv6."
)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
CANONICAL_URL = "https://www.linkedin.com/jobs/view/{}"

# Mirrors the jobspy linkedin-global + linkedin-israel matrices: iOS/macOS across
# the EU (deep pull), Canada, and Israel. (query, location, results_wanted)
QUERIES = ["iOS", "macOS"]
LOCATIONS = [
    ("European Union", 75),
    ("Canada", 25),
    ("Israel", 25),
]

PER_PAGE = 10  # LinkedIn returns 10 cards per guest search page.
POLITE_DELAY = 0.3  # seconds between requests, to stay under rate limits.
MAX_CONSECUTIVE_429 = 3  # turn descriptions off only after sustained rate-limiting.

# One card per <li>; each opens with <div class="base-card ...">.
_CARD_SPLIT_RE = re.compile(r'(?=<li>\s*<div class="base-card)')
_ID_RE = re.compile(r'jobPosting:(\d+)')
_TITLE_RE = re.compile(r'base-search-card__title">\s*(.*?)\s*</h3>', re.DOTALL)
_COMPANY_LINK_RE = re.compile(r'base-search-card__subtitle">\s*<a[^>]*>\s*(.*?)\s*</a>', re.DOTALL)
_COMPANY_SPAN_RE = re.compile(r'base-search-card__subtitle"[^>]*>\s*(.*?)\s*</h4>', re.DOTALL)
_LOCATION_RE = re.compile(r'job-search-card__location">\s*(.*?)\s*</span>', re.DOTALL)
_DATETIME_RE = re.compile(r'datetime="([^"]+)"')
_DESC_RE = re.compile(r'show-more-less-html__markup[^>]*>(.*?)</div>', re.DOTALL)


def _clean(text):
    """Strip tags, collapse whitespace, unescape HTML entities."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return _html.unescape(re.sub(r"\s+", " ", text)).strip()


def _first(pattern, text):
    match = pattern.search(text or "")
    return match.group(1) if match else ""


class _RateLimited(Exception):
    """Raised when LinkedIn returns 429 after a retry — signal to back off."""


def _get(url, params, timeout, verbose):
    """GET with one 429 retry. Returns (status, text) or raises _RateLimited / HTTPError."""
    try:
        return http_request(url, params=params, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            time.sleep(2.0)
            try:
                return http_request(url, params=params, timeout=timeout)
            except urllib.error.HTTPError as exc2:
                if exc2.code == 429:
                    raise _RateLimited()
                raise
        raise


@register(DESCRIPTION, default_enabled=False)
class LinkedInGuestSource(BaseSource):
    name = NAME

    def fetch(self, verbose=False):
        # Read-only peek at dedup state: skip the per-job description request for
        # jobs we've already seen (they get deduped downstream anyway). On a
        # steady-state daily run almost everything is already seen, so this keeps
        # the request count — and the rate-limit risk — low. Imported lazily here
        # (not at module top) so the state package never imports this source
        # module in a cycle.
        try:
            from ..state.seen_jobs import load_seen_jobs
            path = getattr(self, "seen_jobs_file", None)
            seen = load_seen_jobs(path) if path else load_seen_jobs()
            seen = seen or set()
        except Exception:
            seen = set()

        timeout = 30
        global_budget = self._env_float("SCRAPE_BUDGET_SECONDS", 600.0)
        budget = self._env_float("LINKEDIN_BUDGET_SECONDS", max(60.0, global_budget * 0.85))
        deadline = time.monotonic() + budget

        jobs = []
        seen_ids = set()  # de-dupe overlapping query/location hits within this run
        descriptions_disabled = False
        consecutive_429 = 0

        for query in QUERIES:
            for location, results_wanted in LOCATIONS:
                if time.monotonic() >= deadline:
                    self._timed_out("LinkedIn source time budget reached")
                    if verbose:
                        print("[{}] time budget reached — returning {} job(s)".format(NAME, len(jobs)), flush=True)
                    return jobs
                pages = (results_wanted + PER_PAGE - 1) // PER_PAGE
                if verbose:
                    print("[{}] {!r} in {} ({} page(s))...".format(NAME, query, location, pages), flush=True)
                for page in range(pages):
                    if time.monotonic() >= deadline:
                        self._timed_out("LinkedIn source time budget reached")
                        return jobs
                    start = page * PER_PAGE
                    try:
                        status, html = _get(
                            SEARCH_URL,
                            {"keywords": query, "location": location, "start": start},
                            timeout, verbose,
                        )
                        self._attempt_http(status)
                    except _RateLimited:
                        self._attempt_failed("HTTP 429 rate limit")
                        if verbose:
                            print("[{}] rate-limited on search — stopping this location".format(NAME), flush=True)
                        break
                    except Exception as exc:
                        self._attempt_failed(exc)
                        if verbose:
                            print("[{}] search error ({} start={}): {}".format(NAME, location, start, exc), flush=True)
                        break

                    cards = [c for c in _CARD_SPLIT_RE.split(html) if "jobPosting:" in c]
                    if not cards:
                        break  # no more results for this query/location
                    time.sleep(POLITE_DELAY)

                    for card in cards:
                        job_id = _first(_ID_RE, card)
                        if not job_id or job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)

                        title = _clean(_first(_TITLE_RE, card))
                        if not title:
                            continue
                        company = _clean(_first(_COMPANY_LINK_RE, card)) or _clean(_first(_COMPANY_SPAN_RE, card))
                        loc = _clean(_first(_LOCATION_RE, card))
                        raw_date = _first(_DATETIME_RE, card)
                        posted = None
                        if raw_date:
                            try:
                                posted = parse_iso_date(raw_date[:10])
                            except Exception:
                                posted = None
                        url = CANONICAL_URL.format(job_id)
                        is_remote = "remote" in loc.lower() or "remote" in title.lower()

                        # Only fetch the (expensive) description for genuinely-new jobs.
                        already_seen = not seen.isdisjoint(job_identity_keys({
                            "url": url,
                            "title": title,
                            "company": company,
                            "location": loc,
                        }))
                        description = ""
                        if not already_seen and not descriptions_disabled and time.monotonic() < deadline:
                            try:
                                _s, page_html = _get(JOB_URL.format(job_id), None, timeout, verbose)
                                description = _clean(_first(_DESC_RE, page_html))
                                consecutive_429 = 0  # a success clears the streak
                                time.sleep(POLITE_DELAY)
                            except _RateLimited:
                                # Tolerate transient throttling: back off and keep trying
                                # the next job. Only give up on descriptions after a
                                # sustained streak — a circuit breaker, not a hair trigger.
                                consecutive_429 += 1
                                time.sleep(min(5.0 * consecutive_429, 20.0))
                                if consecutive_429 >= MAX_CONSECUTIVE_429:
                                    descriptions_disabled = True
                                    if verbose:
                                        print("[{}] {} consecutive rate-limits — descriptions off for this run".format(NAME, consecutive_429), flush=True)
                                elif verbose:
                                    print("[{}] rate-limited on a description — backing off, retrying next job".format(NAME), flush=True)
                            except Exception:
                                description = ""

                        jobs.append(Job(
                            title=title,
                            company=company,
                            location=loc,
                            url=url,
                            source=NAME,
                            date_posted=posted,
                            description=description,
                            is_remote=is_remote,
                        ))

        if verbose:
            print("[{}] Fetched {} raw jobs".format(NAME, len(jobs)), flush=True)
        return jobs

    @staticmethod
    def _env_float(name, default):
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default
