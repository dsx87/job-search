"""Characterization tests for the stdlib HTTP helpers (mocked transport)."""
import ssl
import urllib.error

import pytest

# --- module under test (repoint on migration) ---
from job_search import http as http_mod
from job_search.config import MAX_RESPONSE_BYTES
from job_search.http import (
    build_url,
    http_json,
    http_request,
    read_capped,
    response_text,
    verbose_source_error,
)


def test_build_url():
    assert build_url("https://x/api") == "https://x/api"
    assert build_url("https://x/api", {"a": "1", "b": "2"}) == "https://x/api?a=1&b=2"
    assert build_url("https://x/api?z=0", {"a": "1"}) == "https://x/api?z=0&a=1"


class _Resp:
    def __init__(self, body, status=200, ctype="application/json; charset=utf-8"):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status
        self._ctype = ctype

    @property
    def headers(self):
        ctype = self._ctype

        class _H:
            def get(self, k, d=""):
                return ctype if k.lower() == "content-type" else d

        return _H()

    def read(self, amt=None):
        # http.client.HTTPResponse.read(amt) — see read_capped.
        return self._body if amt is None else self._body[:amt]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_response_text_respects_charset():
    assert response_text(_Resp("", ctype="text/html; charset=latin-1"), "café".encode("latin-1")) == "café"


def test_http_request_parses_status_and_passes_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["timeout"] = timeout
        return _Resp('{"ok": true}', status=200)

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    status, text = http_request("https://x/api")
    assert status == 200
    assert text == '{"ok": true}'
    assert captured["timeout"] == 30  # default seam value

    http_request("https://x/api", timeout=5)
    assert captured["timeout"] == 5


def test_http_json_parses_body(monkeypatch):
    monkeypatch.setattr(http_mod.urllib.request, "urlopen",
                        lambda r, timeout=None, context=None: _Resp('{"jobs": [1, 2]}'))
    status, data = http_json("https://x/api")
    assert status == 200
    assert data == {"jobs": [1, 2]}


def test_tls_is_verified_by_default(monkeypatch):
    # finding N5: this used to pass ssl._create_unverified_context() for every
    # request, with no recorded reason. All 15 source hosts verify cleanly.
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return _Resp("{}")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    http_request("https://jobs.example.com/api")

    context = captured["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_a_source_can_opt_out_of_verification(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return _Resp("{}")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    http_request("https://broken-cert.example.com/feed", verify_tls=False)
    assert captured["context"].verify_mode == ssl.CERT_NONE


def test_telegram_can_never_opt_out_of_verification(monkeypatch):
    # The bot token travels in the Telegram URL, so a MITM on the Pi's network
    # path could lift it and drive /run and /tailor.
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return _Resp("{}")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    http_request("https://api.telegram.org/botSECRET/getUpdates", verify_tls=False)

    assert captured["context"].verify_mode == ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True


def test_response_read_is_capped(monkeypatch):
    # finding N10: one oversized or chunk-streaming response is an OOM-kill
    # mid-run on a 512 MB Pi.
    oversized = _Resp("x" * (MAX_RESPONSE_BYTES + 5000))
    monkeypatch.setattr(
        http_mod.urllib.request, "urlopen", lambda r, timeout=None, context=None: oversized
    )
    _status, text = http_request("https://x/api")
    assert len(text) == MAX_RESPONSE_BYTES


def test_read_capped_passes_the_limit_through():
    assert read_capped(_Resp("abcdef"), limit=3) == b"abc"


def test_verbose_source_error(capsys):
    verbose_source_error("src", True, urllib.error.HTTPError("u", 503, "x", {}, None))
    out = capsys.readouterr().out
    assert "[src] HTTP 503" in out

    verbose_source_error("src", True, RuntimeError("boom"))
    assert "[src] Error: boom" in capsys.readouterr().out

    verbose_source_error("src", False, RuntimeError("boom"))
    assert capsys.readouterr().out == ""  # silent when not verbose


def test_raw_body_is_sent_verbatim_with_the_given_content_type(monkeypatch):
    # The file-host uploader posts a multipart body it built itself; it must
    # reach the wire unchanged (a re-encode would corrupt the PDF part) and
    # carry the boundary-bearing content type it was built with.
    captured = {}
    payload = b"--B\r\nbinary \x00\xff bytes\r\n--B--\r\n"

    def fake_urlopen(request, timeout=None, context=None):
        captured["request"] = request
        return _Resp("https://x0.at/abc.pdf", ctype="text/plain; charset=utf-8")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    status, text = http_request(
        "https://x0.at/", method="POST", data=payload,
        content_type="multipart/form-data; boundary=B",
    )

    request = captured["request"]
    assert request.data == payload
    assert request.get_header("Content-type") == "multipart/form-data; boundary=B"
    assert status == 200 and text == "https://x0.at/abc.pdf"


def test_raw_body_still_carries_the_browser_user_agent(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["request"] = request
        return _Resp("ok", ctype="text/plain")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    http_request("https://x0.at/", method="POST", data=b"x", content_type="text/plain")

    assert "Mozilla/5.0" in captured["request"].get_header("User-agent")


def test_raw_body_without_a_content_type_sets_none(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["request"] = request
        return _Resp("ok", ctype="text/plain")

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    http_request("https://x/api", method="POST", data=b"x")

    assert captured["request"].data == b"x"
    assert captured["request"].get_header("Content-type") is None


def test_data_and_json_body_together_is_a_programming_error(monkeypatch):
    # Two bodies, one request: silently picking one would send the wrong thing.
    monkeypatch.setattr(
        http_mod.urllib.request, "urlopen",
        lambda r, timeout=None, context=None: _Resp("{}"),
    )
    with pytest.raises(ValueError):
        http_request("https://x/api", method="POST", data=b"x", json_body={"a": 1})


def test_ssl_contexts_are_built_once_and_reused(monkeypatch):
    # Review of PR #7: ssl.create_default_context() loads the system CA store on
    # every call, and a full run makes hundreds of requests — real cost on a Pi.
    monkeypatch.setattr(http_mod, "_CONTEXTS", {})
    builds = []
    real = ssl.create_default_context

    def counting():
        builds.append(1)
        return real()

    monkeypatch.setattr(ssl, "create_default_context", counting)
    monkeypatch.setattr(
        http_mod.urllib.request, "urlopen", lambda r, timeout=None, context=None: _Resp("{}")
    )
    for _ in range(5):
        http_request("https://x/api")
    assert len(builds) == 1
