"""telegra.ph API client (stdlib only, same shape as notify/telegram.py).

Only the four methods the digest needs. Every call is a JSON POST to
``https://api.telegra.ph/<method>``; the API answers 200 with ``{"ok": false,
"error": ...}`` for application errors, which become TelegraphError, and the
usual urllib exceptions for transport errors, which are retried.

``content`` is sent as a JSON *string*, not a nested array: that is the shape
Telegraph documents for form-style parameters, and it is what the service
accepts for a JSON body too.
"""
import json
import socket
import time
import urllib.error

from ..config import RETRYABLE_STATUS
from ..http import http_json

API_BASE = "https://api.telegra.ph"

# Telegraph's documented per-response maximum for getPageList. The index shows
# the 200 most recent digests and nothing older; see the design doc.
PAGE_LIST_LIMIT = 200

# One retry ladder for transport hiccups. Shorter than Telegram's: a failed
# publish is not fatal — the caller falls back to the ZIP — so it is not worth
# holding a run open for a minute.
RETRY_BACKOFF = (2, 6, 0)


class TelegraphError(Exception):
    """The API answered ok=false."""


def _call(method, payload):
    url = "{}/{}".format(API_BASE, method)
    attempts = len(RETRY_BACKOFF)
    for attempt, delay in enumerate(RETRY_BACKOFF, 1):
        try:
            _status, body = http_json(url, method="POST", json_body=payload)
        # HTTPError must be caught before URLError — it is a subclass. Transient
        # statuses (429, 5xx) are retried; permanent ones (4xx) are re-raised.
        # socket.timeout is named explicitly because it is only an alias of TimeoutError
        # from 3.10 and the floor (and the Pi) is 3.9.
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise
            print(
                "    telegraph {} transient error {} — waiting {:g}s "
                "(attempt {}/{})...".format(
                    method, exc.code, delay, attempt, attempts
                ),
                flush=True,
            )
            time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt == attempts:
                raise
            print(
                "    telegraph {} transient network error ({}) — waiting {:g}s "
                "(attempt {}/{})...".format(
                    method, getattr(exc, "reason", exc), delay, attempt, attempts
                ),
                flush=True,
            )
            time.sleep(delay)
            continue
        if not body.get("ok"):
            raise TelegraphError("{}: {}".format(method, body.get("error", "unknown error")))
        return body.get("result") or {}
    raise AssertionError("unreachable")  # pragma: no cover


class TelegraphClient:
    """Stateless: the access token is passed per call, like the API itself."""

    def create_account(self, short_name: str) -> str:
        result = _call("createAccount", {"short_name": short_name})
        return str(result.get("access_token", ""))

    def create_page(self, token: str, title: str, nodes) -> dict:
        return _call("createPage", {
            "access_token": token,
            "title": title,
            "content": json.dumps(nodes, ensure_ascii=False),
        })

    def edit_page(self, token: str, path: str, title: str, nodes) -> dict:
        return _call("editPage", {
            "access_token": token,
            "path": path,
            "title": title,
            "content": json.dumps(nodes, ensure_ascii=False),
        })

    def get_page_list(self, token: str, offset: int = 0) -> list:
        result = _call("getPageList", {
            "access_token": token, "offset": offset, "limit": PAGE_LIST_LIMIT,
        })
        return list(result.get("pages") or [])
