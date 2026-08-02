#!/usr/bin/env python3
"""Publish mock digest pages to telegra.ph so a human can look at them.

Drives the *production* renderer and client — only the data is fake — so what
you open on your phone is exactly what a real run emits. Run it twice with the
same token to watch the long-lived index grow, which is the behaviour no unit
test can show you.

    export TELEGRAPH_PREVIEW_TOKEN=...     # printed on first run
    python scripts/telegraph_preview.py --days 3

The preview account is deliberately separate from the production one: mock
digests must never land in the real index.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from job_search.digest.fixtures import sample_context  # noqa: E402
from job_search.digest.publish import publish_digest  # noqa: E402
from job_search.digest.telegraph import render_digest_nodes, render_index_nodes  # noqa: E402
from job_search.notify.telegraph import TelegraphClient  # noqa: E402

PREVIEW_SHORT_NAME = "job-search-preview"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3,
                        help="how many mock digests to publish (default 3)")
    parser.add_argument("--dump", default="",
                        help="write the node JSON to this path instead of publishing")
    parser.add_argument("--force", action="store_true",
                        help="allow running against TELEGRAPH_ACCESS_TOKEN (never do this)")
    return parser.parse_args(argv)


def _resolve_token(args, client):
    """The preview token, minting one when absent. '' means refuse to run."""
    token = os.environ.get("TELEGRAPH_PREVIEW_TOKEN", "").strip()
    if token:
        if token == os.environ.get("TELEGRAPH_ACCESS_TOKEN", "").strip() and not args.force:
            print("Refusing to run: TELEGRAPH_PREVIEW_TOKEN equals TELEGRAPH_ACCESS_TOKEN. "
                  "Mock digests must not land in your real index (--force overrides).",
                  file=sys.stderr)
            return ""
        return token
    if os.environ.get("TELEGRAPH_ACCESS_TOKEN", "").strip() and not args.force:
        print("Refusing to run: TELEGRAPH_ACCESS_TOKEN is set but TELEGRAPH_PREVIEW_TOKEN is "
              "not. Set TELEGRAPH_PREVIEW_TOKEN to a separate preview account so mock digests "
              "do not land in your real index (--force overrides).", file=sys.stderr)
        return ""
    token = client.create_account(PREVIEW_SHORT_NAME)
    print("Minted a preview account. Export this to reuse it (and keep the index alive):")
    print("  export TELEGRAPH_PREVIEW_TOKEN={}".format(token))
    return token


def main(argv=None, client=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    client = client or TelegraphClient()

    if args.dump:
        ctx = sample_context()
        payload = {
            "digest": render_digest_nodes(ctx, index_url="https://telegra.ph/example-index"),
            "index": render_index_nodes([
                {"title": "Job Digest 2026-08-01 deadbeef", "url": "https://telegra.ph/example"},
            ]),
        }
        with open(args.dump, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print("Wrote {}".format(args.dump))
        return 0

    token = _resolve_token(args, client)
    if not token:
        return 2

    today = datetime.date.today()
    for offset in range(args.days):
        date = today - datetime.timedelta(days=offset)
        url = publish_digest(client, token, sample_context(date), date)
        print("{}  {}".format(date.isoformat(), url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
