"""Stdlib HTTP helpers shared by the sources.

http_request grows an explicit ``timeout`` seam (defaulting to the config value)
so callers/tests can inject it; the default is unchanged.

Two safety properties (findings N5 and N10):

* **TLS is verified.** This used to call ``ssl._create_unverified_context()``
  for every request, with no recorded reason. Since then the Telegram control bot
  started reaching ``api.telegram.org`` through here **with the bot token in the
  URL**, so anyone on the Pi's network path could lift the token and drive /run
  and /tailor. A source that genuinely needs to skip verification can pass
  ``verify_tls=False``; hosts in ``_ALWAYS_VERIFY_HOSTS`` ignore that request.
* **Responses are size-capped.** An unbounded ``read()`` on a 512 MB Pi is one
  oversized or chunk-streaming response away from an OOM-kill mid-run.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .config import HTTP_TIMEOUT_SECONDS, MAX_RESPONSE_BYTES

# Hosts where skipping certificate verification is never acceptable, whatever the
# caller passes: credentials travel in the URL/body.
_ALWAYS_VERIFY_HOSTS = frozenset({"api.telegram.org"})


def read_capped(response, limit=MAX_RESPONSE_BYTES):
    """Read at most ``limit`` bytes from ``response``.

    ``read(n)`` stops after n bytes instead of buffering an entire (possibly
    endless) body. Truncation is silent by design: the parsers already tolerate
    partial documents by failing that one source, and a hard failure here would
    take down a whole run over one misbehaving board.
    """
    return response.read(limit)


# Built once and reused: ssl.create_default_context() loads the system CA store
# on every call, and a full run makes hundreds of requests (MAX_WORKERS=8 across
# ~15 sources, plus a per-description fetch each) — real cost on the Pi. An
# SSLContext is safe to share across threads. Built lazily so importing this
# module (the scraper CLI does, just for the timeout constant) stays cheap; a
# benign race just builds one twice.
_CONTEXTS = {}


def _ssl_context(url, verify_tls):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    verified = bool(verify_tls) or host in _ALWAYS_VERIFY_HOSTS
    context = _CONTEXTS.get(verified)
    if context is None:
        context = _CONTEXTS[verified] = (
            ssl.create_default_context() if verified else ssl._create_unverified_context()
        )
    return context


def build_url(url, params=None):
    if not params:
        return url
    query = urllib.parse.urlencode(params, doseq=True)
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return url + separator + query


def response_text(response, raw):
    charset = None
    content_type = response.headers.get("content-type", "")
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).strip("\"'")
    if not charset:
        charset = "utf-8"
    return raw.decode(charset, "replace")


def http_request(
    url,
    params=None,
    method="GET",
    json_body=None,
    headers=None,
    timeout=HTTP_TIMEOUT_SECONDS,
    verify_tls=True,
    data=None,
    content_type=None,
):
    """Perform one request and return ``(status, decoded_text)``.

    ``json_body`` serializes a JSON body; ``data`` sends pre-built bytes
    verbatim under ``content_type`` (the file-host uploader's multipart body,
    which must not be re-encoded). Passing both is a programming error rather
    than a silent pick of one.
    """
    if data is not None and json_body is not None:
        raise ValueError("http_request takes either data or json_body, not both")
    request_url = build_url(url, params=params)
    body = None
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 PortableJobScraper/1.0"
        ),
        "Accept": "application/json, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    }
    if headers:
        request_headers.update(headers)
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    elif data is not None:
        body = data
        if content_type:
            request_headers["Content-Type"] = content_type

    request = urllib.request.Request(
        request_url,
        data=body,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=_ssl_context(request_url, verify_tls),
    ) as response:
        raw = read_capped(response)
        return response.status, response_text(response, raw)


def http_json(url, params=None, method="GET", json_body=None, headers=None, verify_tls=True):
    status, text = http_request(
        url,
        params=params,
        method=method,
        json_body=json_body,
        headers=headers,
        verify_tls=verify_tls,
    )
    return status, json.loads(text)


def verbose_source_error(source_name, verbose, exc):
    if verbose:
        if isinstance(exc, urllib.error.HTTPError):
            print("[{}] HTTP {}".format(source_name, exc.code))
        else:
            print("[{}] Error: {}".format(source_name, exc))
