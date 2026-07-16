"""Seen-jobs dedup state.

The on-disk format (a sorted JSON list, indent=2) is load-bearing: the daily
workflow's set-union merge across the orphan `state` branch parses it directly.
load_seen_jobs returns None (not an empty set) when the file is absent — the
first-run sentinel main uses to silence jobs older than 7 days.
"""
import base64
import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from ..config import SEEN_JOBS_FILE
from ..identity import job_identity_keys, normalize_url, title_company_key


_DELIVERY_PREFIX = "delivery:"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DeliveryRetryState:
    attempt: int = 0
    retry_on: Optional[datetime.date] = None
    notified: bool = False
    blocked: bool = False


def _identity_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def delivery_identity_tokens(
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    **_ignored,
):
    """Return stable, union-safe identities for a posting's URL and job key."""
    values = job_identity_keys({
        "url": url,
        "title": title,
        "company": company,
        "location": location,
    })
    return tuple(_identity_token(value) for value in values)


def delivery_retry_state(
    seen: set,
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    **_ignored,
) -> DeliveryRetryState:
    """Derive the strongest valid retry state after arbitrary set-union merges."""
    tokens = set(delivery_identity_tokens(url, title, company, location))
    attempts = []
    notified = False
    blocked = False
    for marker in seen:
        if not isinstance(marker, str) or not marker.startswith(_DELIVERY_PREFIX):
            continue
        parts = marker.split(":")
        if len(parts) == 3 and parts[1] == "notified" and parts[2] in tokens:
            notified = True
        elif len(parts) == 3 and parts[1] == "blocked" and parts[2] in tokens:
            blocked = True
        elif len(parts) == 5 and parts[1] == "attempt" and parts[2] in tokens:
            try:
                attempt = int(parts[3])
            except ValueError:
                continue
            if attempt not in (1, 2, 3):
                continue
            if parts[4] == "blocked":
                if attempt == 3:
                    attempts.append((attempt, None))
                    blocked = True
                continue
            try:
                retry_on = datetime.date.fromisoformat(parts[4])
            except ValueError:
                continue
            attempts.append((attempt, retry_on))

    if not attempts:
        return DeliveryRetryState(notified=notified, blocked=blocked)
    highest = max(attempt for attempt, _retry_on in attempts)
    dates = [retry_on for attempt, retry_on in attempts if attempt == highest and retry_on]
    retry_on = max(dates) if dates and not blocked else None
    return DeliveryRetryState(highest, retry_on, notified, blocked)


def mark_delivery_notified(
    seen: set,
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    **_ignored,
) -> None:
    for token in delivery_identity_tokens(url, title, company, location):
        seen.add(f"delivery:notified:{token}")


def _bounded(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _encode_alert(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_alert(value: str):
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def record_delivery_failure(seen: set, job: dict, today: datetime.date, stage: str) -> DeliveryRetryState:
    """Record one bounded failed attempt and return the newly derived state."""
    tokens = delivery_identity_tokens(**job)
    current = delivery_retry_state(seen, **job)
    if not tokens or current.blocked:
        return current
    attempt = min(current.attempt + 1, 3)
    if attempt == 1:
        status = (today + datetime.timedelta(days=1)).isoformat()
    elif attempt == 2:
        status = (today + datetime.timedelta(days=2)).isoformat()
    else:
        status = "blocked"
    for token in tokens:
        seen.add(f"delivery:attempt:{token}:{attempt}:{status}")
        if attempt == 3:
            seen.add(f"delivery:blocked:{token}")

    if attempt == 3:
        primary = tokens[0]
        payload = {
            "title": _bounded(job.get("title"), 180),
            "company": _bounded(job.get("company"), 180),
            "url": _bounded(job.get("url"), 500),
            "stage": _bounded(stage, 40),
            "guidance": "Use /tailor to retry this job manually.",
        }
        seen.add(f"delivery:block-alert:{primary}:{_encode_alert(payload)}")
    return delivery_retry_state(seen, **job)


def pending_block_alerts(seen: set):
    """Return valid unacknowledged terminal-alert payloads in stable order."""
    alerted = {
        marker.split(":", 2)[2]
        for marker in seen
        if isinstance(marker, str) and marker.startswith("delivery:block-alerted:")
    }
    pending = {}
    for marker in sorted(seen):
        if not isinstance(marker, str) or not marker.startswith("delivery:block-alert:"):
            continue
        parts = marker.split(":", 3)
        if len(parts) != 4 or not _TOKEN_RE.match(parts[2]) or parts[2] in alerted:
            continue
        payload = _decode_alert(parts[3])
        if payload is not None:
            pending.setdefault(parts[2], payload)
    return sorted(pending.items())


def acknowledge_block_alert(seen: set, token: str) -> None:
    if _TOKEN_RE.match(str(token or "")):
        seen.add(f"delivery:block-alerted:{token}")


def load_seen_jobs():
    """Returns a set of seen URL keys, or None if the state file doesn't exist (first run)."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return None
    with open(SEEN_JOBS_FILE) as f:
        return set(json.load(f))


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)
