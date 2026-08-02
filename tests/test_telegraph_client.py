"""TDD for the telegra.ph API client."""
import json
import urllib.error

import pytest

from job_search.notify import telegraph


class FakeUrlopen:
    """Queue of responses (or exceptions) for urllib.request.urlopen."""

    def __init__(self, items):
        self._items = list(items)
        self.requests = []

    def __call__(self, request, timeout=None, context=None):
        self.requests.append(request)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(result):
    from tests.conftest import FakeHTTPResponse

    return FakeHTTPResponse(json.dumps({"ok": True, "result": result}))


def _err(message):
    from tests.conftest import FakeHTTPResponse

    return FakeHTTPResponse(json.dumps({"ok": False, "error": message}))


def _install(monkeypatch, items):
    fake = FakeUrlopen(items)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(telegraph.time, "sleep", lambda _s: None)
    return fake


def test_create_page_posts_json_to_the_right_endpoint(monkeypatch):
    fake = _install(monkeypatch, [_ok({"path": "P-08-01", "url": "https://telegra.ph/P-08-01"})])

    page = telegraph.TelegraphClient().create_page("tok", "Title", [{"tag": "p", "children": ["hi"]}])

    assert page["url"] == "https://telegra.ph/P-08-01"
    request = fake.requests[0]
    assert request.full_url == "https://api.telegra.ph/createPage"
    assert request.method == "POST"
    body = json.loads(request.data.decode("utf-8"))
    assert body["access_token"] == "tok"
    assert body["title"] == "Title"
    # content is a JSON *string* of the node array — Telegraph's documented shape.
    assert json.loads(body["content"]) == [{"tag": "p", "children": ["hi"]}]


def test_api_error_raises_telegraph_error(monkeypatch):
    _install(monkeypatch, [_err("TITLE_REQUIRED")])

    with pytest.raises(telegraph.TelegraphError) as excinfo:
        telegraph.TelegraphClient().create_page("tok", "", [])

    assert "TITLE_REQUIRED" in str(excinfo.value)


def test_transient_network_error_is_retried_then_succeeds(monkeypatch):
    fake = _install(monkeypatch, [
        urllib.error.URLError("connection reset"),
        _ok({"path": "P-08-01", "url": "https://telegra.ph/P-08-01"}),
    ])

    page = telegraph.TelegraphClient().create_page("tok", "Title", [])

    assert page["path"] == "P-08-01"
    assert len(fake.requests) == 2


def test_retries_are_bounded_and_the_last_error_propagates(monkeypatch):
    fake = _install(monkeypatch, [urllib.error.URLError("down")] * len(telegraph.RETRY_BACKOFF))

    with pytest.raises(urllib.error.URLError):
        telegraph.TelegraphClient().create_page("tok", "Title", [])

    assert len(fake.requests) == len(telegraph.RETRY_BACKOFF)


def test_create_account_returns_the_token(monkeypatch):
    _install(monkeypatch, [_ok({"access_token": "abc123", "short_name": "job-search"})])

    assert telegraph.TelegraphClient().create_account("job-search") == "abc123"


def test_get_page_list_asks_for_the_documented_maximum(monkeypatch):
    pages = [{"path": "p{}".format(i), "title": "T{}".format(i), "url": "u{}".format(i)}
             for i in range(3)]
    fake = _install(monkeypatch, [_ok({"total_count": 3, "pages": pages})])

    result = telegraph.TelegraphClient().get_page_list("tok")

    assert [p["path"] for p in result] == ["p0", "p1", "p2"]
    body = json.loads(fake.requests[0].data.decode("utf-8"))
    assert body["limit"] == telegraph.PAGE_LIST_LIMIT == 200


def test_edit_page_sends_the_path(monkeypatch):
    fake = _install(monkeypatch, [_ok({"path": "Index-08-01", "url": "https://telegra.ph/Index"})])

    telegraph.TelegraphClient().edit_page("tok", "Index-08-01", "Index", [])

    body = json.loads(fake.requests[0].data.decode("utf-8"))
    assert body["path"] == "Index-08-01"
