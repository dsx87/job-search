"""Characterization tests for the stdlib HTTP helpers (mocked transport)."""
import ssl
import urllib.error

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
