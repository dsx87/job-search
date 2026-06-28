"""Playwright-backed source (secrettelaviv).

Cloudflare-fronted, so it drives a headless Chromium via Playwright. Playwright
is imported lazily inside fetch(), so the module imports cleanly (and the
registry builds) even when Playwright/Chromium is not installed.
"""
from ..filters.rules import dedup
from ..http import build_url
from .base import BaseSource, register
from .parsers import parse_link_jobs


@register(
    "Secret Tel Aviv (Cloudflare-fronted): Israel-focused English listings via "
    "Playwright/Chromium. Skips unless Playwright is installed.",
    optional_dependency="playwright",
)
class SecretTelAvivSource(BaseSource):
    # Cloudflare-fronted: returns 403 to stdlib urllib from datacenter IPs, so we
    # drive a real headless Chromium via Playwright (verified to get HTTP 200).
    # Skips automatically if Playwright/Chromium isn't installed.
    name = "secrettelaviv"
    BASE_URL = "https://jobs.secrettelaviv.com"
    SEARCH_URL = BASE_URL + "/list/find/"
    QUERIES = ["ios", "swift", "macos", "mobile developer"]
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def fetch(self, verbose=False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if verbose:
                print("[secrettelaviv] Skipped: Playwright not installed")
            return []

        jobs = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.USER_AGENT, locale="en-US")
                try:
                    for query in self.QUERIES:
                        url = build_url(self.SEARCH_URL, params={"q": query})
                        page = context.new_page()
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=45000)
                            page.wait_for_timeout(3000)  # let any CF check settle
                            html = page.content()
                            jobs.extend(
                                parse_link_jobs(
                                    html,
                                    self.BASE_URL,
                                    ["/job/"],
                                    self.name,
                                    default_remote=False,
                                )
                            )
                        except Exception as exc:
                            if verbose:
                                print("[secrettelaviv] Error for query={!r}: {}".format(query, exc))
                        finally:
                            page.close()
                finally:
                    browser.close()
        except Exception as exc:
            if verbose:
                print("[secrettelaviv] Playwright error: {}".format(exc))
            return dedup(jobs)

        jobs = dedup(jobs)
        if verbose:
            print("[secrettelaviv] Fetched {} raw jobs".format(len(jobs)))
        return jobs
