"""Characterization tests for the stdlib HTTP helpers (mocked transport)."""
import urllib.error

# --- module under test (repoint on migration) ---
from job_search import http as http_mod
from job_search.http import build_url, response_text, http_request, http_json, verbose_source_error


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

    def read(self):
        return self._body

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


def test_verbose_source_error(capsys):
    verbose_source_error("src", True, urllib.error.HTTPError("u", 503, "x", {}, None))
    out = capsys.readouterr().out
    assert "[src] HTTP 503" in out

    verbose_source_error("src", True, RuntimeError("boom"))
    assert "[src] Error: boom" in capsys.readouterr().out

    verbose_source_error("src", False, RuntimeError("boom"))
    assert capsys.readouterr().out == ""  # silent when not verbose
