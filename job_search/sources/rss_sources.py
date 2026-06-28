"""RSS-feed job sources (fetched via http_request)."""
import xml.etree.ElementTree as ET

from ..dates import parse_email_date
from ..http import http_request, verbose_source_error
from ..models import Job
from .base import BaseSource, register
from .parsers import parse_rss_jobs


@register("We Work Remotely RSS feeds.")
class WeWorkRemotelySource(BaseSource):
    name = "weworkremotely"
    FEEDS = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    ]

    def fetch(self, verbose=False):
        jobs = []
        for feed_url in self.FEEDS:
            try:
                status, text = http_request(feed_url)
                if status != 200:
                    if verbose:
                        print("[weworkremotely] HTTP {} for {}".format(status, feed_url))
                    continue
                root = ET.fromstring(text)
            except Exception as exc:
                if verbose:
                    print("[weworkremotely] Error fetching {}: {}".format(feed_url, exc))
                continue

            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = item.findtext("description") or ""
                pub_date = item.findtext("pubDate") or ""
                region = (item.findtext("region") or "").strip()
                country = (item.findtext("country") or "").strip()
                company = ""
                if ": " in title:
                    company, title = title.split(": ", 1)
                jobs.append(
                    Job(
                        title=title,
                        company=company,
                        location=country or region or "Remote",
                        url=link,
                        source=self.name,
                        date_posted=parse_email_date(pub_date),
                        description=desc,
                        is_remote=True,
                    )
                )

        if verbose:
            print("[weworkremotely] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Remote First Jobs RSS.")
class RemoteFirstJobsSource(BaseSource):
    name = "remotefirstjobs"
    RSS_URL = "https://remotefirstjobs.com/rss/jobs.rss"

    def fetch(self, verbose=False):
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[remotefirstjobs] HTTP {}".format(status))
                return []
            jobs = parse_rss_jobs(text, source=self.name, default_location="Remote")
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return []
        if verbose:
            print("[remotefirstjobs] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("Remote Vibe RSS.")
class RemoteVibeSource(BaseSource):
    name = "remotevibe"
    RSS_URL = "https://remotevibecodingjobs.com/feed.xml"

    def fetch(self, verbose=False):
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[remotevibe] HTTP {}".format(status))
                return []
            jobs = parse_rss_jobs(text, source=self.name, default_location="Remote")
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return []
        if verbose:
            print("[remotevibe] Fetched {} raw jobs".format(len(jobs)))
        return jobs


@register("SwissDevJobs RSS.")
class SwissDevJobsSource(BaseSource):
    name = "swissdevjobs"
    RSS_URL = "https://swissdevjobs.ch/rss"

    def fetch(self, verbose=False):
        jobs = []
        try:
            status, text = http_request(self.RSS_URL)
            if status != 200:
                if verbose:
                    print("[swissdevjobs] HTTP {}".format(status))
                return jobs
            root = ET.fromstring(text)
        except Exception as exc:
            verbose_source_error(self.name, verbose, exc)
            return jobs

        for item in root.iter("item"):
            title_raw = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            pub_date = item.findtext("pubDate") or ""
            company = ""
            title = title_raw
            if " @ " in title_raw:
                title, rest = title_raw.split(" @ ", 1)
                parts = rest.split(" - ")
                company = parts[0].strip()
            jobs.append(
                Job(
                    title=title.strip(),
                    company=company,
                    location="Switzerland",
                    url=link,
                    source=self.name,
                    date_posted=parse_email_date(pub_date),
                    description=desc,
                )
            )

        if verbose:
            print("[swissdevjobs] Fetched {} raw jobs".format(len(jobs)))
        return jobs
