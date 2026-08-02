"""TDD for publishing a digest page and maintaining the rolling index."""
import datetime

import pytest

from job_search.digest import publish
from job_search.digest.fixtures import sample_context
from job_search.digest.telegraph import INDEX_TITLE
from job_search.notify.telegraph import PAGE_LIST_LIMIT, TelegraphError

DATE = datetime.date(2026, 8, 1)


class FakeClient:
    """Records calls; serves a page list that grows as pages are created.

    ``get_page_list`` honours ``offset`` the way the real API does: a window
    of at most ``PAGE_LIST_LIMIT`` entries starting at ``offset``, shorter
    only when it runs past the end of ``pages`` -- the real termination
    signal the discovery walk relies on.
    """

    def __init__(self, pages=None, fail=None):
        self.pages = list(pages or [])
        self.fail = fail or {}
        self.calls = []

    def _maybe_fail(self, method):
        exc = self.fail.get(method)
        if exc:
            raise exc

    def get_page_list(self, token, offset=0):
        self.calls.append(("get_page_list", token, offset))
        self._maybe_fail("get_page_list")
        return list(self.pages[offset:offset + PAGE_LIST_LIMIT])

    def create_page(self, token, title, nodes):
        self.calls.append(("create_page", title, nodes))
        self._maybe_fail("create_page")
        page = {"path": title.replace(" ", "-"), "title": title,
                "url": "https://telegra.ph/" + title.replace(" ", "-")}
        self.pages.insert(0, page)
        return page

    def edit_page(self, token, path, title, nodes):
        self.calls.append(("edit_page", path, title, nodes))
        self._maybe_fail("edit_page")
        return {"path": path, "title": title, "url": "https://telegra.ph/" + path}

    def methods(self):
        return [call[0] for call in self.calls]


def test_publish_creates_the_index_then_the_digest_then_refreshes():
    client = FakeClient()

    url = publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    assert url.startswith("https://telegra.ph/Job-Digest-2026-08-01-")
    assert client.methods() == ["get_page_list", "create_page", "create_page",
                                "get_page_list", "edit_page"]
    # First create_page is the index, second is the digest.
    assert client.calls[1][1] == INDEX_TITLE
    assert client.calls[2][1].startswith("Job Digest 2026-08-01 ")


def test_existing_index_is_reused_not_duplicated():
    index = {"path": "Job-Search-Digests", "title": INDEX_TITLE,
             "url": "https://telegra.ph/Job-Search-Digests"}
    client = FakeClient(pages=[index])

    publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    created = [c for c in client.calls if c[0] == "create_page"]
    assert len(created) == 1
    assert created[0][1] != INDEX_TITLE


def test_digest_page_links_back_to_the_index():
    index = {"path": "Job-Search-Digests", "title": INDEX_TITLE,
             "url": "https://telegra.ph/Job-Search-Digests"}
    client = FakeClient(pages=[index])

    publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    digest_nodes = [c for c in client.calls if c[0] == "create_page"][0][2]
    assert "https://telegra.ph/Job-Search-Digests" in repr(digest_nodes)


def test_refreshed_index_links_the_new_digest_and_never_links_itself():
    client = FakeClient()

    publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    _method, _path, _title, nodes = [c for c in client.calls if c[0] == "edit_page"][0]
    # The one <ul> holds exactly the digests: the index must not link itself.
    items = [n for n in nodes if n["tag"] == "ul"][0]["children"]
    labels = [item["children"][0]["children"][0] for item in items]
    assert labels == ["Job Digest 2026-08-01"]


def test_create_page_failure_propagates_so_the_caller_can_fall_back():
    client = FakeClient(fail={"create_page": TelegraphError("createPage: FLOOD_WAIT")})

    with pytest.raises(TelegraphError):
        publish.publish_digest(client, "tok", sample_context(DATE), DATE)


def test_index_lookup_failure_still_publishes_the_digest(capsys):
    client = FakeClient(fail={"get_page_list": RuntimeError("api down")})

    url = publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    assert url.startswith("https://telegra.ph/Job-Digest-")
    assert "api down" in capsys.readouterr().err


def test_index_refresh_failure_is_logged_not_raised(capsys):
    index = {"path": "Job-Search-Digests", "title": INDEX_TITLE, "url": "https://telegra.ph/i"}
    client = FakeClient(pages=[index], fail={"edit_page": RuntimeError("edit down")})

    url = publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    assert url.startswith("https://telegra.ph/Job-Digest-")
    assert "edit down" in capsys.readouterr().err


def test_title_can_be_pinned_for_deterministic_tests():
    client = FakeClient()

    publish.publish_digest(client, "tok", sample_context(DATE), DATE,
                           title="Job Digest 2026-08-01 deadbeef")

    assert client.calls[2][1] == "Job Digest 2026-08-01 deadbeef"


# ── index discovery beyond the first getPageList window ─────────────────────
# The index page is created once, before any digest, and only ever edited in
# place -- so it is permanently the OLDEST page in the account. Once the
# account holds more than PAGE_LIST_LIMIT pages, a discovery walk that only
# ever looks at offset=0 stops seeing it and starts creating a duplicate index
# every run, silently orphaning the old one.

def _filler_pages(count, *, start=0):
    """``count`` non-index pages, oldest-looking title, never matching INDEX_TITLE."""
    return [{"path": "filler-{}".format(i), "title": "Job Digest filler {}".format(i),
             "url": "https://telegra.ph/filler-{}".format(i)}
            for i in range(start, start + count)]


def test_index_beyond_the_first_page_of_getpagelist_is_still_found():
    index = {"path": "Job-Search-Digests", "title": INDEX_TITLE,
             "url": "https://telegra.ph/Job-Search-Digests"}
    # Newest-first order, as the real API returns it: 220 digests newer than
    # the index, so the index sits past the first PAGE_LIST_LIMIT-sized window.
    client = FakeClient(pages=_filler_pages(220) + [index])

    url = publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    assert url.startswith("https://telegra.ph/Job-Digest-")
    created = [c for c in client.calls if c[0] == "create_page"]
    assert len(created) == 1                     # only the digest -- no duplicate index
    assert created[0][1] != INDEX_TITLE
    discovery_offsets = [c[2] for c in client.calls if c[0] == "get_page_list"]
    assert discovery_offsets[0] == 0
    assert PAGE_LIST_LIMIT in discovery_offsets   # a second window was actually requested


def test_index_discovery_stops_after_the_first_short_window():
    """An account under one window's worth of pages: the walk must not keep
    requesting empty windows after the first short (end-of-account) reply."""
    client = FakeClient(pages=_filler_pages(30))

    publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    discovery_calls = [c for c in client.calls if c[0] == "get_page_list"]
    # One short window during discovery (index absent, so a new one is
    # created) plus one more during the refresh that follows -- never more.
    assert len(discovery_calls) == 2
    assert discovery_calls[0][2] == 0


def test_index_discovery_hard_stops_and_logs_when_never_found(capsys):
    """A misbehaving API that keeps returning full windows and never yields
    the index (and never returns a short, end-of-account window either) must
    not be walked forever."""
    huge = _filler_pages(publish._MAX_INDEX_WINDOWS * PAGE_LIST_LIMIT + PAGE_LIST_LIMIT)
    client = FakeClient(pages=huge)

    url = publish.publish_digest(client, "tok", sample_context(DATE), DATE)

    assert url.startswith("https://telegra.ph/Job-Digest-")
    discovery_calls = [c for c in client.calls if c[0] == "get_page_list"]
    # Exactly the hard-stop windows during discovery, plus one for the refresh
    # that follows the (new, since the walk gave up) index it created.
    assert len(discovery_calls) == publish._MAX_INDEX_WINDOWS + 1
    discovery_offsets = [c[2] for c in discovery_calls[:publish._MAX_INDEX_WINDOWS]]
    assert discovery_offsets == [i * PAGE_LIST_LIMIT for i in range(publish._MAX_INDEX_WINDOWS)]
    assert capsys.readouterr().err.strip() != ""
