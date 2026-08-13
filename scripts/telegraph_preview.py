#!/usr/bin/env python3
"""Publish mock digest pages to telegra.ph so a human can look at them.

Drives the *production* renderer and client — only the data is fake — so what
you open on your phone is exactly what a real run emits. Run it twice with the
same token to watch the long-lived index grow, which is the behaviour no unit
test can show you.

    export TELEGRAPH_PREVIEW_TOKEN=...     # printed on first run
    python scripts/telegraph_preview.py --days 3
    python scripts/telegraph_preview.py --days 1 --upload

``--upload`` additionally encrypts the mock CVs and puts them on the real file
host, so the page's "Download CV" links are live. That is the only way to check
end to end — before trusting a scheduled run — that the downloaded file keeps
its clean name, opens with the password and refuses without it. Without the
flag the page carries the fixtures' real-shaped but fake links and nothing
leaves the machine.

The preview account is deliberately separate from the production one: mock
digests must never land in the real index.
"""
import argparse
import datetime
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from job_search.digest.fixtures import sample_context  # noqa: E402
from job_search.digest.publish import publish_digest  # noqa: E402
from job_search.digest.telegraph import render_digest_nodes, render_index_nodes  # noqa: E402
from job_search.latex.encrypt import encrypt_pdf, new_password  # noqa: E402
from job_search.notify.telegraph import TelegraphClient  # noqa: E402
from job_search.notify.x0 import X0Client  # noqa: E402

PREVIEW_SHORT_NAME = "job-search-preview"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3,
                        help="how many mock digests to publish (default 3)")
    parser.add_argument("--dump", default="",
                        help="write the node JSON to this path instead of publishing")
    parser.add_argument("--force", action="store_true",
                        help="suppress the refusal below and proceed with a preview account")
    parser.add_argument("--upload", action="store_true",
                        help="encrypt the mock CVs and put them on the real file host, so "
                             "the page's download links are live (they expire in ~100 days)")
    return parser.parse_args(argv)


def _upload_cvs(ctx):
    """Host the fixture CVs for real and rewrite the context's links.

    Deliberately mirrors pipeline/run.py::_publish_cvs rather than importing it:
    that one is wired into the run's state machine and its failure handling, and
    what a preview needs to exercise is the encrypt → upload → link chain.
    """
    password = new_password()
    host = X0Client()
    encrypted = []
    for entry in ctx.fits:
        payload = encrypt_pdf(entry.pdf_bytes, password)
        entry.cv_url = host.upload(entry.cv_filename, payload)
        encrypted.append((entry.cv_filename, payload))
        print("  {}  {}".format(entry.cv_filename, entry.cv_url))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in encrypted:
            archive.writestr(name, payload)
    iso = ctx.date.isoformat() if hasattr(ctx.date, "isoformat") else str(ctx.date)
    ctx.cv_zip_url = host.upload("job-cvs-{}.zip".format(iso), buffer.getvalue())
    ctx.cv_encrypted = True
    print("  all CVs           {}".format(ctx.cv_zip_url))
    # Printed, not published: in a real run this reaches the user over Telegram
    # and never touches the page.
    print("  CV password: {}".format(password))
    return password


def _resolve_token(args, client):
    """The preview token, minting one when absent. '' means refuse to run."""
    token = os.environ.get("TELEGRAPH_PREVIEW_TOKEN", "").strip()
    if token:
        if token == os.environ.get("TELEGRAPH_ACCESS_TOKEN", "").strip() and not args.force:
            print("Refusing to run: TELEGRAPH_PREVIEW_TOKEN equals TELEGRAPH_ACCESS_TOKEN, so "
                  "this would not be a separate preview account. Mock digests must not land in "
                  "your real index (--force proceeds anyway, reusing this same token).",
                  file=sys.stderr)
            return ""
        return token
    if os.environ.get("TELEGRAPH_ACCESS_TOKEN", "").strip() and not args.force:
        print("Refusing to run: TELEGRAPH_ACCESS_TOKEN is set but TELEGRAPH_PREVIEW_TOKEN is "
              "not. Set TELEGRAPH_PREVIEW_TOKEN to a separate preview account so mock digests "
              "do not land in your real index (--force proceeds with a separate preview "
              "account instead).", file=sys.stderr)
        return ""
    token = client.create_account(PREVIEW_SHORT_NAME)
    if not token:
        print("Telegraph returned no access token; cannot publish.", file=sys.stderr)
        return ""
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
        # Explicit UTF-8: the nodes carry emoji and "·", and ensure_ascii=False
        # keeps them literal, so the locale default would raise under LANG=C.
        with open(args.dump, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print("Wrote {}".format(args.dump))
        return 0

    token = _resolve_token(args, client)
    if not token:
        return 2

    today = datetime.date.today()
    for offset in range(args.days):
        date = today - datetime.timedelta(days=offset)
        ctx = sample_context(date)
        if args.upload:
            _upload_cvs(ctx)
        url = publish_digest(client, token, ctx, date)
        print("{}  {}".format(date.isoformat(), url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
