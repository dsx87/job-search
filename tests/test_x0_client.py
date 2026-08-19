"""TDD for the x0.at file-host client (modelled on test_telegraph_client.py)."""
import urllib.error

import pytest

from job_search.notify import x0


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


def _body(text):
    from tests.conftest import FakeHTTPResponse

    return FakeHTTPResponse(text, headers={"content-type": "text/plain; charset=UTF-8"})


def _install(monkeypatch, items):
    fake = FakeUrlopen(items)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(x0.time, "sleep", lambda _s: None)
    return fake


def test_upload_posts_multipart_with_keep_name_and_a_long_id(monkeypatch):
    fake = _install(monkeypatch, [_body("https://x0.at/igor_pivnyk_cv_acme_AbCd.pdf\n")])

    url = x0.X0Client().upload("igor_pivnyk_cv_acme.pdf", b"%PDF-1.4 ciphertext")

    assert url == "https://x0.at/igor_pivnyk_cv_acme_AbCd.pdf"
    request = fake.requests[0]
    assert request.full_url == x0.API_BASE
    assert request.get_method() == "POST"
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    body = request.data
    # keep_name is the whole point of the hashless filenames: without it the
    # link (and so the downloaded file) gets a random name.
    assert b'name="keep_name"' in body and b"1" in body
    assert b'name="id_length"' in body and b"24" in body
    assert b'name="file"; filename="igor_pivnyk_cv_acme.pdf"' in body
    assert b"%PDF-1.4 ciphertext" in body


def test_the_response_body_is_used_verbatim(monkeypatch):
    # Rather than reconstructing the URL from the filename: keep_name sanitises
    # the name server-side and the id layout is the host's business.
    _install(monkeypatch, [_body("  https://x0.at/weird_Name-2_xyz.pdf  \n")])

    assert x0.X0Client().upload("weird name!.pdf", b"x") == "https://x0.at/weird_Name-2_xyz.pdf"


def test_a_non_url_body_is_an_error_not_a_link(monkeypatch):
    # An error page must never become an href on the published digest.
    _install(monkeypatch, [_body("<html>rate limited</html>")])

    with pytest.raises(x0.FileHostError):
        x0.X0Client().upload("cv.pdf", b"x")


def test_a_plain_http_body_is_refused(monkeypatch):
    _install(monkeypatch, [_body("http://x0.at/cv.pdf")])

    with pytest.raises(x0.FileHostError):
        x0.X0Client().upload("cv.pdf", b"x")


def test_an_empty_body_is_refused(monkeypatch):
    _install(monkeypatch, [_body("   ")])

    with pytest.raises(x0.FileHostError):
        x0.X0Client().upload("cv.pdf", b"x")


def test_a_transient_status_is_retried(monkeypatch):
    fake = _install(monkeypatch, [
        urllib.error.HTTPError("https://x0.at/", 503, "unavailable", {}, None),
        _body("https://x0.at/cv_abc.pdf"),
    ])

    assert x0.X0Client().upload("cv.pdf", b"x") == "https://x0.at/cv_abc.pdf"
    assert len(fake.requests) == 2


def test_a_network_error_is_retried(monkeypatch):
    fake = _install(monkeypatch, [
        urllib.error.URLError("connection reset"),
        _body("https://x0.at/cv_abc.pdf"),
    ])

    assert x0.X0Client().upload("cv.pdf", b"x") == "https://x0.at/cv_abc.pdf"
    assert len(fake.requests) == 2


def test_a_permanent_status_is_not_retried(monkeypatch):
    fake = _install(monkeypatch, [
        urllib.error.HTTPError("https://x0.at/", 400, "bad request", {}, None),
        _body("https://x0.at/cv_abc.pdf"),
    ])

    with pytest.raises(urllib.error.HTTPError):
        x0.X0Client().upload("cv.pdf", b"x")
    assert len(fake.requests) == 1


def test_the_last_attempt_re_raises(monkeypatch):
    attempts = len(x0.RETRY_BACKOFF)
    fake = _install(monkeypatch, [
        urllib.error.HTTPError("https://x0.at/", 503, "unavailable", {}, None)
    ] * attempts)

    with pytest.raises(urllib.error.HTTPError):
        x0.X0Client().upload("cv.pdf", b"x")
    assert len(fake.requests) == attempts


def test_a_bad_body_is_not_retried(monkeypatch):
    # A garbage body is the service answering, not a transient blip: retrying
    # would only leave a second orphaned copy on the host.
    fake = _install(monkeypatch, [_body("nope"), _body("https://x0.at/cv_abc.pdf")])

    with pytest.raises(x0.FileHostError):
        x0.X0Client().upload("cv.pdf", b"x")
    assert len(fake.requests) == 1
