#!/usr/bin/env python3
"""One-off feasibility probe: can a real Chromium (Playwright) reach the
Cloudflare-fronted sources from a GitHub Actions runner?

landing.jobs and secrettelaviv return HTTP 403 to the stdlib urllib scraper on
CI (they work from residential IPs). This checks whether driving a real browser
gets past Cloudflare, or whether it's a hard datacenter-IP block that no browser
can bypass. Delete this file + the probe workflow once we have the answer.
"""

import sys

from playwright.sync_api import sync_playwright

TARGETS = [
    (
        "landing.jobs",
        "https://landing.jobs/jobs?q=ios%20OR%20macos%20OR%20swiftui%20OR%20uikit&hd=true&page=1",
    ),
    (
        "secrettelaviv",
        "https://jobs.secrettelaviv.com/list/find/?q=ios",
    ),
]

CHALLENGE_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "checking your browser")


def probe(ctx, name, url):
    page = ctx.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        initial_status = resp.status if resp else None
        # Give a Cloudflare JS/managed challenge a chance to resolve and reload.
        page.wait_for_timeout(8000)
        body = page.content()
        low = body.lower()
        challenge = any(m in low for m in CHALLENGE_MARKERS)
        title = page.title()
        verdict = "BLOCKED" if (initial_status == 403 and challenge) or (challenge) else (
            "OK" if initial_status and initial_status < 400 else "MAYBE"
        )
        print(f"[{name}] verdict={verdict} initial_status={initial_status} "
              f"challenge_page={challenge} title={title!r} body_len={len(body)}", flush=True)
        snippet = " ".join(body[:400].split())
        print(f"[{name}] snippet: {snippet}", flush=True)
    except Exception as exc:
        print(f"[{name}] ERROR: {type(exc).__name__}: {exc}", flush=True)
    finally:
        page.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        for name, url in TARGETS:
            probe(ctx, name, url)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
