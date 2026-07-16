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
from ..identity import identity_keys_from_fields, normalize_url, title_company_key
from ..text import collapse_ws


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
    values = identity_keys_from_fields(url, title, company, location)
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


# ── Structured evaluation lifecycle (audit order 4d) ─────────────────────────
# Like the delivery markers above, evaluation-lifecycle facts are stored as
# union-safe marker strings keyed by identity token, so they survive the daily
# set-union merge across the `state` branch. A job keeps its permanent identity
# keys (= "seen"); these markers add the content/criteria signature, verdict,
# and first/last-seen dates so a corrected description or a criteria change can
# reopen a job for re-evaluation instead of suppressing it forever. Legacy
# string-only seen entries carry no such markers and stay suppressed.
_EVAL_PREFIX = "eval:"
_VALID_VERDICTS = frozenset({"fit", "nonfit", "deferred"})
_DIGIT_RE = re.compile(r"\d+")


def _short_hash(*parts: str) -> str:
    """A short, stable, colon-free hex fingerprint of the joined parts."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _normalize_content(content: str) -> str:
    """Case-fold, drop digit runs, and collapse whitespace.

    Digits are removed so cosmetic listing churn — "Over 214 applicants",
    "Reposted 3 days ago", "iOS 15" → "iOS 16" — does not flip the signature and
    force a needless re-evaluation every run. Substantive textual corrections
    (added requirements, "US residents only", changed platform) still change it.
    Known tradeoff: a purely numeric-threshold edit ("10+ years" → "3+ years",
    "$90k" → "$140k") will not reopen a prior non-fit; a criteria-version bump is
    the escape hatch that forces global re-evaluation.
    """
    return collapse_ws(_DIGIT_RE.sub(" ", str(content or "").lower()))


def criteria_version(criteria: str) -> str:
    """Return a short, whitespace-insensitive fingerprint of the fit criteria."""
    return _short_hash(collapse_ws(criteria))


def evaluation_signature(content: str, criteria_version: str = "") -> str:
    """Return a short signature of a posting's content under a criteria version."""
    return _short_hash(_normalize_content(content), str(criteria_version or ""))


@dataclass(frozen=True)
class LifecycleRecord:
    known: bool = False
    signatures: frozenset = frozenset()
    verdicts: frozenset = frozenset()
    first_seen: Optional[datetime.date] = None
    last_seen: Optional[datetime.date] = None


def lifecycle_record(
    seen: set,
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    **_ignored,
) -> LifecycleRecord:
    """Derive the structured evaluation record for one identity from the set."""
    tokens = set(delivery_identity_tokens(url, title, company, location))
    if not tokens:
        return LifecycleRecord()
    signatures = set()
    verdicts = set()
    firsts = []
    lasts = []
    known = False
    for marker in seen:
        if not isinstance(marker, str) or not marker.startswith(_EVAL_PREFIX):
            continue
        parts = marker.split(":")
        if len(parts) < 4 or parts[2] not in tokens:
            continue
        kind = parts[1]
        if kind == "sig" and len(parts) == 4:
            known = True
            signatures.add(parts[3])
        elif kind == "verdict" and len(parts) == 5:
            known = True
            if parts[4] in _VALID_VERDICTS:
                verdicts.add(parts[4])
        elif kind in ("first", "last") and len(parts) == 4:
            try:
                day = datetime.date.fromisoformat(parts[3])
            except ValueError:
                continue
            known = True
            (firsts if kind == "first" else lasts).append(day)
    return LifecycleRecord(
        known=known,
        signatures=frozenset(signatures),
        verdicts=frozenset(verdicts),
        first_seen=min(firsts) if firsts else None,
        last_seen=max(lasts) if lasts else None,
    )


def record_evaluation(seen: set, job, signature: str, verdict: str, today: datetime.date) -> None:
    """Record one evaluation outcome as union-safe lifecycle markers."""
    tokens = delivery_identity_tokens(**job)
    if not tokens:
        return
    day = today.isoformat()
    for token in tokens:
        seen.add(f"eval:sig:{token}:{signature}")
        seen.add(f"eval:verdict:{token}:{signature}:{verdict}")
        seen.add(f"eval:first:{token}:{day}")
        seen.add(f"eval:last:{token}:{day}")


def should_reevaluate(seen: set, job, signature: str) -> bool:
    """True when a seen job should be re-evaluated because its content or the
    criteria changed since a prior non-fit/deferred verdict (a reopen)."""
    record = lifecycle_record(seen, **job)
    if not record.known:
        return False
    if signature in record.signatures:
        return False
    if "fit" in record.verdicts:
        return False
    return True


def load_seen_jobs():
    """Returns a set of seen URL keys, or None if the state file doesn't exist (first run)."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return None
    with open(SEEN_JOBS_FILE) as f:
        return set(json.load(f))


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)
