"""x0.at file-host client (stdlib only, same shape as notify/telegraph.py).

telegra.ph cannot host files, so the digest page carries *links* and the PDFs
live here. Everything uploaded is already AES-256 encrypted (see
``latex/encrypt.py``) — on this host the link is the credential, and the page
carrying it is public to anyone who has its URL.

Why x0.at, verified live 2026-08-12: the response body **is** the URL in plain
text, ``keep_name=1`` keeps the real filename in it, ``id_length=24`` makes the
id unguessable, and the bytes are served directly with no interstitial and no
cookie gate. Retention is size-derived —
``MIN_AGE + (MAX_AGE-MIN_AGE)*(1-(SIZE/MAX_SIZE))^2`` over 3/100 days with a
1024 MiB cap — so a ~50 KB PDF sits near the 100-day maximum.

**No deletion.** The Null Pointer software this runs returns an ``X-Token``
header for later management, but x0.at does not: a throwaway upload during
implementation came back with no such header, on the POST or on the later GET.
So an upload is permanent until it expires, and every failure path here relies
on expiry plus an unguessable id rather than retracting anything.

Retries are *less* conservative than telegraph.py's createPage, and for the
opposite reason. There, a retry after a committed write publishes a duplicate
page that is permanent and listed in the public index. Here it leaves an orphan
blob on a short-retention host under a 24-character random id — harmless, and
nothing links to it. A lost digest costs a whole run, so the retry is worth it.
"""
import socket
import time
import urllib.error

from ..config import RETRYABLE_STATUS
from ..http import http_request
from . import multipart

API_BASE = "https://x0.at/"

# Same ladder as telegraph.py: two quick retries, then give up and let the
# caller fall back to the ZIP.
RETRY_BACKOFF = (2, 6, 0)

# What the size formula above works out to for a CV-sized file. Duplicated as a
# plain constant in digest/telegraph.py, which must stay free of network
# imports; keep the two in step.
LINK_TTL_DAYS = 100

# Uploads are slower than the JSON calls this project otherwise makes, and a
# handful of them run back to back.
UPLOAD_TIMEOUT_SECONDS = 120


class FileHostError(Exception):
    """The host answered, but not with a usable URL."""


class X0Client:
    """One method, no state — the host has no accounts and no sessions."""

    def upload(self, filename: str, content: bytes) -> str:
        """POST ``content`` as ``filename``; return the URL it is served from.

        The body is returned verbatim (stripped) rather than reconstructed from
        the filename, so the host's own sanitisation of ``keep_name`` and any
        change to its id layout cannot drift out of sync with what we publish.
        """
        body, content_type = multipart.encode(
            {"keep_name": "1", "id_length": "24"},
            {"file": (filename, content)},
        )
        attempts = len(RETRY_BACKOFF)
        for attempt, delay in enumerate(RETRY_BACKOFF, 1):
            try:
                _status, text = http_request(
                    API_BASE, method="POST", data=body, content_type=content_type,
                    timeout=UPLOAD_TIMEOUT_SECONDS,
                )
            # HTTPError must be caught before URLError — it is a subclass.
            # socket.timeout is named explicitly because it is only an alias of
            # TimeoutError from 3.10 and the floor (and the Pi) is 3.9.
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                    raise
                print(
                    "    x0 upload transient error {} — waiting {:g}s "
                    "(attempt {}/{})...".format(exc.code, delay, attempt, attempts),
                    flush=True,
                )
                time.sleep(delay)
                continue
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt == attempts:
                    raise
                print(
                    "    x0 upload transient network error ({}) — waiting {:g}s "
                    "(attempt {}/{})...".format(
                        getattr(exc, "reason", exc), delay, attempt, attempts
                    ),
                    flush=True,
                )
                time.sleep(delay)
                continue
            url = str(text or "").strip()
            # A rejection, an error page or a maintenance notice all arrive as a
            # 200 with a body. Publishing one as an href would put a dead
            # "Download CV" link on the page; failing here takes the ZIP instead.
            if not url.startswith("https://") or "\n" in url or " " in url:
                raise FileHostError(
                    "x0.at answered with something other than a URL: {!r}".format(url[:200])
                )
            return url
        raise AssertionError("unreachable")  # pragma: no cover
