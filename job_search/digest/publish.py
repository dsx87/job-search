"""Publish a run's digest to telegra.ph and keep the rolling index current.

Split from telegraph.py so that module stays a pure, network-free renderer:
everything here talks to a client, and every test drives it with a fake one.

Failure policy, which the pipeline depends on:

* ``create_page`` for the digest raising is the *only* failure that escapes.
  The caller catches it and falls back to the ZIP.
* Looking up or refreshing the index never escapes. A digest page without a
  back-link, or an index a day out of date, is worth far less than the run.
"""
import secrets
import sys

from .telegraph import INDEX_TITLE, digest_page_title, render_digest_nodes, render_index_nodes
# The digest module otherwise stays free of any network-facing import; this
# one module is the exception, since it is the one that talks to a client.
from ..notify.telegraph import PAGE_LIST_LIMIT

# The index page is created once, before any digest, and is only ever edited
# in place -- so it is permanently the OLDEST page in the account. getPageList
# returns newest-first, so once the account holds more than PAGE_LIST_LIMIT
# pages the index falls out of the first window and discovery must walk
# further windows to find it. This caps that walk so a misbehaving API (one
# that keeps returning full windows and never a short, end-of-account one)
# cannot loop forever: PAGE_LIST_LIMIT * this many windows is far beyond what
# years of daily runs would ever produce.
_MAX_INDEX_WINDOWS = 50


def _index_title():
    """A fresh index title, randomized the same way digest titles are.

    A bare, predictable ``INDEX_TITLE`` derives a guessable telegra.ph path
    that links every digest ever published -- defeating the random token on
    each digest title. The random suffix here closes that hole (finding 1).
    """
    return "{} {}".format(INDEX_TITLE, secrets.token_hex(4))


def _find_index(client, token):
    """Walk getPageList windows until the index turns up or the account runs
    out of pages. Returns None if neither happens within the hard stop.

    Matches by prefix, not equality: the index title now carries a random
    suffix (see ``_index_title``), and an account may still hold an index
    created before that change, under the bare ``INDEX_TITLE`` marker. Both
    must be found so a run never mints a duplicate index.
    """
    offset = 0
    for _ in range(_MAX_INDEX_WINDOWS):
        pages = client.get_page_list(token, offset=offset)
        for page in pages:
            if str(page.get("title", "")).startswith(INDEX_TITLE):
                return page
        if len(pages) < PAGE_LIST_LIMIT:
            return None  # short window: reached the end of the account
        offset += PAGE_LIST_LIMIT
    print("  Telegraph index walk gave up after {} windows without finding it"
          .format(_MAX_INDEX_WINDOWS), file=sys.stderr)
    return None


def _index_page(client, token):
    """The account's index page, creating it when absent. None on any failure."""
    try:
        index = _find_index(client, token)
        if index is not None:
            return index
        return client.create_page(token, _index_title(), render_index_nodes([]))
    except Exception as exc:
        print("  Telegraph index unavailable (digest still published): {}".format(exc),
              file=sys.stderr)
        return None


def _refresh_index(client, token, index):
    """Rebuild the index from the account's current page list. Never raises.

    Only the first window: the index content itself is capped at the
    INDEX_LIMIT most recent digests (see render_index_nodes), so the newest
    PAGE_LIST_LIMIT page is already everything a refresh could ever use.
    Once the account is large the index will not be *in* that window (it is
    the oldest page there is) so the path filter below is a no-op rather than
    something that needs the same multi-window walk as discovery.

    The edit reuses the index's OWN title (falling back to the bare marker
    only if it is somehow missing) rather than always writing ``INDEX_TITLE``
    -- otherwise every refresh would strip the random suffix a fresh index
    was created with, undoing finding 1's fix on the very next run.
    """
    if not index:
        return
    try:
        pages = client.get_page_list(token)
        digests = [p for p in pages if p.get("path") != index.get("path")]
        title = index.get("title") or INDEX_TITLE
        client.edit_page(token, index["path"], title, render_index_nodes(digests))
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
