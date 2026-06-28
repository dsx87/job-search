"""HTML-scraping job sources (fetched via http_request, parsed with regex/JSON)."""
import html
import json
import re

from ..dates import parse_epoch_date
from ..filters.rules import dedup
from ..http import http_request, verbose_source_error
from ..location.db import COUNTRY_NAMES
from ..models import Job
from .base import BaseSource, register
from .parsers import (
    JOBSCROLLER_CARD_RE,
    MOBILECAREER_JOB_OBJECT_RE,
    decode_mobilecareer_job_object,
    jobscroller_card_values,
    jobscroller_relative_date,
    mobilecareer_date,
    mobilecareer_description,
    mobilecareer_location,
    parse_link_jobs,
)


@register("Arc remote iOS/Swift pages.")
class ArcSource(BaseSource):
    name = "arc"
    BASE_URL = "https://arc.dev"
    PATHS = ("mobile-ios", "swift")
    NEXT_RE = re.compile(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
        re.DOTALL | re.IGNORECASE,
    )

    def fetch(self, verbose=False):
        jobs = []
        seen = set()
        for path in self.PATHS:
            url = "{}/remote-jobs/{}".format(self.BASE_URL, path)
            try:
                status, text = http_request(url)
                if status != 200:
                    if verbose:
                        print("[arc] HTTP {} for {}".format(status, path))
                    continue
            except Exception as exc:
                if verbose:
                    print("[arc] Error fetching {}: {}".format(path, exc))
                continue

            for job in self.parse_html(text):
                key = job.url or "{}|{}".format(job.title, job.company)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)

        if verbose:
            print("[arc] Fetched {} raw jobs".format(len(jobs)))
        return jobs

    def parse_html(self, html_text):
        data = self.next_data(html_text)
        if data is None:
            return []

        jobs = []
        seen = set()
        for item in self.walk_jobs(data):
            key = str(item.get("randomKey") or item.get("urlString") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            jobs.append(self.to_job(item))
        return jobs

    def next_data(self, html_text):
        match = self.NEXT_RE.search(html_text)
        if not match:
            return None
        raw = match.group(1).strip()
        for candidate in (raw, html.unescape(raw)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        return None

    def walk_jobs(self, obj):
        found = []
        if isinstance(obj, dict):
            if set(["randomKey", "title", "company", "urlString"]) <= set(obj.keys()):
                found.append(obj)
            for value in obj.values():
                found.extend(self.walk_jobs(value))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(self.walk_jobs(item))
        return found

    def to_job(self, item):
        company = item.get("company") or {}
        category_names = []
        for cat in item.get("categories", []) or []:
            if isinstance(cat, dict) and cat.get("name"):
                category_names.append(cat.get("name", ""))
        levels = item.get("experienceLevels", []) or []
        countries = item.get("requiredCountries", []) or []
        location = self.country_location(countries)
        return Job(
            title=item.get("title", ""),
            company=company.get("name", "") if isinstance(company, dict) else "",
            location=location,
            url=self.job_url(item),
            source=self.name,
            date_posted=parse_epoch_date(item.get("postedAt")),
            description=" ".join(
                part
                for part in [
                    item.get("positionType", ""),
                    item.get("jobType", ""),
                    " ".join(str(x) for x in levels),
                    " ".join(category_names),
                ]
                if part
            ),
            is_remote=True,
        )

    def job_url(self, item):
        url_string = str(item.get("urlString") or "").strip("/")
        random_key = str(item.get("randomKey") or "")
        if not url_string:
            return "{}/remote-jobs".format(self.BASE_URL)
        slug = url_string
        if random_key and not slug.endswith("-{}".format(random_key)):
            slug = "{}-{}".format(slug, random_key)
        route = "details" if self.is_arc_exclusive(item) else "j"
        return "{}/remote-jobs/{}/{}".format(self.BASE_URL, route, slug)

    def is_arc_exclusive(self, item):
        return "jobRole" in item or "availableHoursPerWeek" in item

    def country_location(self, countries):
        if not countries:
            return "Remote"
        names = [COUNTRY_NAMES.get(code, code) for code in countries]
        return "Remote - " + ", ".join(names)


@register("Mobile.Career iOS jobs with direct company/ATS apply links.")
class MobileCareerSource(BaseSource):
    name = "mobile.career"
    IOS_URL = "https://mobile.career/ios-developer-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, text = http_request(self.IOS_URL)
            if status != 200:
                if verbose:
                    print("[mobile.career] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        seen = set()
        for match in MOBILECAREER_JOB_OBJECT_RE.finditer(text):
            item = decode_mobilecareer_job_object(match.group(0))
            if not item:
                continue

            job_id = str(item.get("id") or "")
            slug = str(item.get("slug") or "")
            key = job_id or slug
            if not key or key in seen:
                continue
            seen.add(key)

            url = str(item.get("applyTo") or "")
            if not url and slug:
                url = "https://mobile.career/jobs/{}".format(slug)

            location = mobilecareer_location(item)
            location_type = str(item.get("locationType") or "").upper()
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=url,
                    source=self.name,
                    date_posted=mobilecareer_date(
                        item.get("publishedWebAt") or item.get("createdAt")
                    ),
                    description=mobilecareer_description(item),
                    is_remote=location_type == "REMOTE",
                )
            )

        if verbose:
            print("[mobile.career] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("JobScroller Swift/Objective-C company-career-page listings.")
class JobScrollerSource(BaseSource):
    name = "jobscroller"
    ROLE_URLS = (
        "https://www.jobscroller.net/roles/swift",
        "https://www.jobscroller.net/roles/objective-c",
    )

    def fetch(self, verbose=False):
        jobs = []
        seen = set()
        for role_url in self.ROLE_URLS:
            try:
                status, text = http_request(role_url)
                if status != 200:
                    if verbose:
                        print("[jobscroller] HTTP {} for {}".format(status, role_url))
                    continue
            except Exception as exc:
                if verbose:
                    print("[jobscroller] Error fetching {}: {}".format(role_url, exc))
                continue

            for match in JOBSCROLLER_CARD_RE.finditer(text):
                href = match.group(1)
                if href in seen:
                    continue
                seen.add(href)

                company, title, location, relative_date, description = jobscroller_card_values(
                    match.group(2)
                )
                if not title:
                    continue

                jobs.append(
                    Job(
                        title=title,
                        company=company,
                        location=location,
                        url="https://www.jobscroller.net{}".format(href),
                        source=self.name,
                        date_posted=jobscroller_relative_date(relative_date),
                        description=description,
                        is_remote="remote" in location.lower(),
                    )
                )

        if verbose:
            print("[jobscroller] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Relocate.me search pages, best effort with stdlib HTML parsing.")
class RelocateMeSource(BaseSource):
    name = "relocate.me"
    BASE_URL = "https://relocate.me"
    SEARCH_URL = BASE_URL + "/search"
    QUERIES = ["ios", "macos", "swiftui"]

    def fetch(self, verbose=False):
        jobs = []
        for query in self.QUERIES:
            try:
                status, text = http_request(self.SEARCH_URL, params={"q": query})
                if status != 200:
                    if verbose:
                        print("[relocate.me] HTTP {} for query={!r}".format(status, query))
                    continue
                jobs.extend(
                    parse_link_jobs(
                        text,
                        self.BASE_URL,
                        ["/job/", "/jobs/", "/vacancy/"],
                        self.name,
                        default_remote=False,
                    )
                )
            except Exception as exc:
                if verbose:
                    print("[relocate.me] Error for query={!r}: {}".format(query, exc))
                continue

        jobs = dedup(jobs)
        if verbose:
            print("[relocate.me] Fetched {} raw jobs".format(len(jobs)))
        return jobs
