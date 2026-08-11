#!/usr/bin/env python3
"""Mint a telegra.ph access token for the digest pages.

Run once; paste the token into the GitHub secret and the Pi's .env as
TELEGRAPH_ACCESS_TOKEN. Telegraph has no way to recover a lost token — losing it
means the next run creates a fresh account and a fresh (empty) index page, so
keep it somewhere durable.

    python scripts/telegraph_account.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from job_search.notify.telegraph import TelegraphClient  # noqa: E402


def main() -> int:
    token = TelegraphClient().create_account("job-search")
    if not token:
        print("Telegraph returned no access token.", file=sys.stderr)
        return 1
    print("TELEGRAPH_ACCESS_TOKEN={}".format(token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
