"""Stdlib HTTP helpers shared by the sources.

http_request grows an explicit ``timeout`` seam (defaulting to the config value)
so callers/tests can inject it; the default is unchanged.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .config import HTTP_TIMEOUT_SECONDS


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


def http_request(url, params=None, method="GET", json_body=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
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

    request = urllib.request.Request(
        request_url,
        data=body,
        headers=request_headers,
        method=method,
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=context,
    ) as response:
        raw = response.read()
        return response.status, response_text(response, raw)


def http_json(url, params=None, method="GET", json_body=None, headers=None):
    status, text = http_request(
        url,
        params=params,
        method=method,
        json_body=json_body,
        headers=headers,
    )
    return status, json.loads(text)


def verbose_source_error(source_name, verbose, exc):
    if verbose:
        if isinstance(exc, urllib.error.HTTPError):
            print("[{}] HTTP {}".format(source_name, exc.code))
        else:
            print("[{}] Error: {}".format(source_name, exc))
