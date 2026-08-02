"""Publish a run's digest to telegra.ph and keep the rolling index current.

Split from telegraph.py so that module stays a pure, network-free renderer:
everything here talks to a client, and every test drives it with a fake one.

Failure policy, which the pipeline depends on:

* ``create_page`` for the digest raising is the *only* failure that escapes.
  The caller catches it and falls back to the ZIP.
* Looking up or refreshing the index never escapes. A digest page without a
  back-link, or an index a day out of date, is worth far less than the run.
"""
import sys

from .telegraph import INDEX_TITLE, digest_page_title, render_digest_nodes, render_index_nodes


def _index_page(client, token):
    """The account's index page, creating it when absent. None on any failure."""
    try:
        pages = client.get_page_list(token)
        for page in pages:
            if str(page.get("title", "")) == INDEX_TITLE:
                return page
        return client.create_page(token, INDEX_TITLE, render_index_nodes([]))
    except Exception as exc:
        print("  Telegraph index unavailable (digest still published): {}".format(exc),
              file=sys.stderr)
        return None


def _refresh_index(client, token, index):
    """Rebuild the index from the account's current page list. Never raises."""
    if not index:
        return
    try:
        pages = client.get_page_list(token)
        digests = [p for p in pages if p.get("path") != index.get("path")]
        client.edit_page(token, index["path"], INDEX_TITLE, render_index_nodes(digests))
    except Exception as exc:
        print("  Telegraph index refresh failed: {}".format(exc), file=sys.stderr)


def publish_digest(client, token, ctx, date, *, title=None) -> str:
    """Publish ``ctx`` as a page and return its URL.

    ``title`` exists so tests can pin the otherwise-random page title.
    """
    index = _index_page(client, token)
    nodes = render_digest_nodes(ctx, index_url=str((index or {}).get("url", "")))
    page = client.create_page(token, title or digest_page_title(date), nodes)
    _refresh_index(client, token, index)
    return str(page.get("url", ""))
