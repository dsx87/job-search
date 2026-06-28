"""JSON-API job sources (fetched via http_json)."""
from ..dates import parse_epoch_date, parse_iso_date
from ..http import http_json, verbose_source_error
from ..models import Job
from .base import BaseSource, register


@register("Remotive remote jobs API.")
class RemotiveSource(BaseSource):
    name = "remotive"
    API_URL = "https://remotive.com/api/remote-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"limit": "100"})
            if status != 200:
                if verbose:
                    print("[remotive] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            tags = item.get("tags", []) or []
            category = item.get("category", "") or ""
            job_type = item.get("job_type", "") or ""
            description = item.get("description", "") or ""
            extra = " ".join(part for part in [category, job_type] + tags if part)
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("candidate_required_location", "") or "Remote",
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_iso_date(item.get("publication_date")),
                    description="{} {}".format(description, extra).strip(),
                    is_remote=True,
                )
            )

        if verbose:
            print("[remotive] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("RemoteOK API.")
class RemoteOKSource(BaseSource):
    name = "remoteok"
    API_URL = "https://remoteok.com/api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL)
            if status != 200:
                if verbose:
                    print("[remoteok] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        items = data[1:] if isinstance(data, list) and len(data) > 1 else []
        for item in items:
            if not isinstance(item, dict):
                continue
            posted = parse_epoch_date(item.get("epoch")) or parse_iso_date(item.get("date"))
            url = item.get("url", "") or ""
            if url and not url.startswith("http"):
                url = "https://remoteok.com{}".format(url)
            tags = item.get("tags", []) or []
            jobs.append(
                Job(
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=item.get("location", "") or "Remote",
                    url=url,
                    source=self.name,
                    date_posted=posted,
                    description="{} {}".format(item.get("description", "") or "", " ".join(tags)),
                    is_remote=True,
                )
            )

        if verbose:
            print("[remoteok] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Jobicy remote jobs API.")
class JobicySource(BaseSource):
    name = "jobicy"
    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"count": "50"})
            if status != 200:
                if verbose:
                    print("[jobicy] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            jobs.append(
                Job(
                    title=item.get("jobTitle", ""),
                    company=item.get("companyName", ""),
                    location=item.get("jobGeo", "") or "Remote",
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_iso_date(item.get("pubDate")),
                    description=item.get("jobDescription", ""),
                    is_remote=True,
                )
            )

        if verbose:
            print("[jobicy] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Arbeitnow visa-sponsorship API.")
class ArbeitnowSource(BaseSource):
    name = "arbeitnow"
    API_URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"visa_sponsorship": "true"})
            if status != 200:
                if verbose:
                    print("[arbeitnow] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("data", []):
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("location", ""),
                    url=item.get("url", ""),
                    source=self.name,
                    date_posted=parse_epoch_date(item.get("created_at")),
                    description=item.get("description", ""),
                    is_remote=bool(item.get("remote", False)),
                )
            )

        if verbose:
            print("[arbeitnow] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("The Muse remote jobs API.")
class TheMuseSource(BaseSource):
    name = "themuse"
    API_URL = "https://www.themuse.com/api/public/jobs"
    MAX_PAGES = 5

    def fetch(self, verbose=False):
        jobs = []
        for page in range(self.MAX_PAGES):
            try:
                status, data = http_json(
                    self.API_URL,
                    params={"page": str(page), "location": "Remote"},
                )
                if status != 200:
                    if verbose:
                        print("[themuse] HTTP {} for page={}".format(status, page))
                    continue
            except Exception as exc:
                verbose_source_error(self.name, verbose, exc)
                return jobs

            jobs.extend(self.parse_items(data.get("results", [])))

            try:
                page_count = int(data.get("page_count") or 0)
            except (TypeError, ValueError):
                page_count = 0
            if page + 1 >= page_count:
                break

        if verbose:
            print("[themuse] Fetched {} raw jobs".format(len(jobs)))
        return jobs

    def parse_items(self, items):
        jobs = []
        for item in items:
            locations = []
            for loc in item.get("locations", []) or []:
                if isinstance(loc, dict) and loc.get("name"):
                    locations.append(loc.get("name", ""))
            categories = []
            for cat in item.get("categories", []) or []:
                if isinstance(cat, dict) and cat.get("name"):
                    categories.append(cat.get("name", ""))
            levels = []
            for level in item.get("levels", []) or []:
                if isinstance(level, dict) and level.get("name"):
                    levels.append(level.get("name", ""))

            company = item.get("company") or {}
            refs = item.get("refs") or {}
            jobs.append(
                Job(
                    title=item.get("name", ""),
                    company=company.get("name", "") if isinstance(company, dict) else "",
                    location=", ".join(locations) or "Remote",
                    url=refs.get("landing_page", "") if isinstance(refs, dict) else "",
                    source=self.name,
                    date_posted=parse_iso_date(item.get("publication_date")),
                    description=" ".join(
                        part
                        for part in [
                            item.get("contents", ""),
                            " ".join(categories),
                            " ".join(levels),
                        ]
                        if part
                    ),
                    is_remote=True,
                )
            )
        return jobs


@register("Himalayas remote jobs API.")
class HimalayasSource(BaseSource):
    name = "himalayas"
    API_URL = "https://himalayas.app/jobs/api"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(self.API_URL, params={"limit": "50"})
            if status != 200:
                if verbose:
                    print("[himalayas] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in data.get("jobs", []):
            categories = item.get("categories", []) or []
            location_restrictions = item.get("locationRestrictions", []) or []
            if location_restrictions:
                location = ", ".join(str(x) for x in location_restrictions)
            else:
                location = "Remote"
            desc = item.get("description", "") or item.get("excerpt", "") or ""
            jobs.append(
                Job(
                    title=item.get("title", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=item.get("applicationLink", "") or item.get("guid", "") or "",
                    source=self.name,
                    date_posted=parse_iso_date(item.get("pubDate")),
                    description="{} {}".format(desc, " ".join(str(x) for x in categories)),
                    is_remote=True,
                )
            )

        if verbose:
            print("[himalayas] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Working Nomads search API.")
class WorkingNomadsSource(BaseSource):
    name = "workingnomads"
    BASE_URL = "https://www.workingnomads.com"
    API_URL = BASE_URL + "/jobsapi/_search"

    def payload(self):
        query = (
            '"ios" OR "ipados" OR "macos" OR "iphone" OR "ipad" OR '
            '"swiftui" OR "uikit" OR "appkit" OR "objective-c" OR '
            '("swift" AND ("ios" OR "macos" OR "swiftui" OR "uikit" OR "xcode"))'
        )
        return {
            "track_total_hits": True,
            "from": 0,
            "size": 100,
            "_source": [
                "company",
                "locations",
                "title",
                "pub_date",
                "description",
                "slug",
                "apply_url",
                "tags",
                "position_type",
                "experience_level",
            ],
            "sort": [{"pub_date": {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": {
                        "query_string": {
                            "query": query,
                            "fields": ["title^2", "description", "company"],
                        }
                    }
                }
            },
            "min_score": 2,
        }

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, data = http_json(
                self.API_URL,
                method="POST",
                json_body=self.payload(),
            )
            if status != 200:
                if verbose:
                    print("[workingnomads] HTTP {}".format(status))
                return jobs
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        hits = data.get("hits", {}).get("hits", [])
        for item in hits:
            source = item.get("_source", {}) if isinstance(item, dict) else {}
            slug = source.get("slug", "") or ""
            if slug:
                url = "{}/jobs/{}".format(self.BASE_URL, slug)
            else:
                url = source.get("apply_url", "") or ""
            tags = source.get("tags", []) or []
            locations = source.get("locations", []) or []
            extra = " ".join(
                part
                for part in [
                    source.get("position_type", ""),
                    source.get("experience_level", ""),
                    " ".join(str(x) for x in tags),
                ]
                if part
            )
            jobs.append(
                Job(
                    title=source.get("title", ""),
                    company=source.get("company", ""),
                    location=", ".join(str(x) for x in locations) or "Remote",
                    url=url,
                    source=self.name,
                    date_posted=parse_iso_date(source.get("pub_date")),
                    description="{} {}".format(source.get("description", ""), extra).strip(),
                    is_remote=True,
                )
            )

        if verbose:
            print("[workingnomads] Fetched {} raw jobs".format(len(jobs)))
        return jobs
