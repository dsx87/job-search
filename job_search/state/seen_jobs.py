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
import tempfile
from dataclasses import dataclass
from typing import Optional

from ..config import SEEN_JOBS_FILE
from ..identity import identity_keys_from_fields, normalize_url, title_company_key
from ..text import collapse_ws


_DELIVERY_PREFIX = "delivery:"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

# Namespaces whose entries are *markers about a job*, not job identities. Every
# such marker is "<namespace>:<kind>:<identity-token>[:...]", which is what makes
# them indexable by token below. Plain identities (a URL, a "title|company" key)
# are answered by ordinary set membership and are never indexed.
_MARKER_NAMESPACES = ("delivery", "eval")

# Marker families worth a second index keyed by kind rather than by token. Only
# the block-alert pair is looked up that way (the alert drain has no token to
# start from), and every extra family costs one more reference per marker on a
# 512 MB box — so this list stays as short as the call sites require.
_INDEXED_KINDS = frozenset({"delivery:block-alert", "delivery:block-alerted"})


class SeenSet(set):
    """A ``set`` that also indexes namespaced markers by identity token and kind.

    Finding N1: ``delivery_retry_state`` and ``lifecycle_record`` each scanned the
    *entire* seen set to answer a question about *one* job, and run_daily calls
    both per fetched candidate — O(candidates x seen_keys). Measured on this Mac
    with 400 candidates: 0.66 s at today's 4.7k keys, 15.7 s at 100k (roughly a
    year of daily runs), where a Pi 1 would spend longer scanning strings than
    calling the LLM.

    The index is maintained on mutation rather than rebuilt, because the pipeline
    adds markers mid-run and re-reads them in the same pass. Every consumer keeps
    a plain-``set`` fallback, so existing call sites (and tests) that pass an
    ordinary set still work — just at the old cost.
    """

    def __init__(self, iterable=()):
        super().__init__(iterable)
        self._by_token = {}
        self._by_kind = {}
        for value in self:
            self._index(value)

    # ── index maintenance ────────────────────────────────────────────────────
    def _index(self, value) -> None:
        if not isinstance(value, str):
            return
        parts = value.split(":", 3)
        if len(parts) < 3 or parts[0] not in _MARKER_NAMESPACES:
            return
        self._by_token.setdefault(parts[2], set()).add(value)
        kind = f"{parts[0]}:{parts[1]}"
        if kind in _INDEXED_KINDS:
            self._by_kind.setdefault(kind, set()).add(value)

    def _deindex(self, value) -> None:
        if not isinstance(value, str):
            return
        parts = value.split(":", 3)
        if len(parts) < 3 or parts[0] not in _MARKER_NAMESPACES:
            return
        for bucket, key in ((self._by_token, parts[2]), (self._by_kind, f"{parts[0]}:{parts[1]}")):
            entries = bucket.get(key)
            if entries is not None:
                entries.discard(value)
                if not entries:
                    del bucket[key]

    # ── lookups ──────────────────────────────────────────────────────────────
    def markers_for(self, token):
        """Markers recorded against one identity token."""
        return self._by_token.get(token, frozenset())

    def markers_of_kind(self, kind):
        """Markers of an indexed "<namespace>:<kind>" family, or None if not indexed."""
        return self._by_kind.get(kind, frozenset()) if kind in _INDEXED_KINDS else None

    def _reindex(self) -> None:
        """Rebuild both indexes from scratch (for bulk removals)."""
        self._by_token.clear()
        self._by_kind.clear()
        for value in self:
            self._index(value)

    # ── mutators ─────────────────────────────────────────────────────────────
    # set's C implementations bypass Python overrides, so EVERY mutating entry
    # point has to be named explicitly — including the ones nothing calls today.
    # A missed one is silent: the set loses a key while _by_token keeps it, and
    # markers_for() then answers with markers for a key that is no longer there,
    # i.e. a wrong dedup answer with no symptom to notice. The bulk removals
    # below reindex rather than diff, because they never run in the hot path.
    #
    # Note that copy(), |, - and friends return a plain `set`, not a SeenSet, so
    # anything derived that way drops to the full-scan fallback — correct, just
    # slower.
    def add(self, value):
        super().add(value)
        self._index(value)

    def update(self, *others):
        for other in others:
            for value in other:
                self.add(value)

    def __ior__(self, other):
        self.update(other)
        return self

    def discard(self, value):
        super().discard(value)
        self._deindex(value)

    def remove(self, value):
        super().remove(value)
        self._deindex(value)

    def pop(self):
        value = super().pop()
        self._deindex(value)
        return value

    def clear(self):
        super().clear()
        self._by_token.clear()
        self._by_kind.clear()

    def difference_update(self, *others):
        super().difference_update(*others)
        self._reindex()

    def intersection_update(self, *others):
        super().intersection_update(*others)
        self._reindex()

    def symmetric_difference_update(self, other):
        super().symmetric_difference_update(other)
        self._reindex()

    def __isub__(self, other):
        self.difference_update(other)
        return self

    def __iand__(self, other):
        self.intersection_update(other)
        return self

    def __ixor__(self, other):
        self.symmetric_difference_update(other)
        return self


def _markers_for_tokens(seen, tokens):
    """The markers worth examining for ``tokens`` — indexed when possible.

    Falls back to the whole set for a plain ``set``; callers re-check the token
    themselves, so both paths produce identical results.
    """
    lookup = getattr(seen, "markers_for", None)
    if lookup is None:
        return seen
    markers = set()
    for token in tokens:
        markers |= lookup(token)
    return markers


def _markers_of_kind(seen, kind):
    """The markers of one kind — indexed when possible, else the whole set."""
    lookup = getattr(seen, "markers_of_kind", None)
    if lookup is None:
        return seen
    indexed = lookup(kind)
    return seen if indexed is None else indexed


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
    for marker in _markers_for_tokens(seen, tokens):
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
        for marker in _markers_of_kind(seen, "delivery:block-alerted")
        if isinstance(marker, str) and marker.startswith("delivery:block-alerted:")
    }
    pending = {}
    for marker in sorted(_markers_of_kind(seen, "delivery:block-alert")):
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
_VALID_VERDICTS = frozenset({"fit", "nonfit", "deferred", "uncertain"})
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


# Hand-bumped whenever job_search/policy.py changes a verdict for the same facts
# (finding N7). Order 4d fingerprints the criteria *text*, but order 6 moved the
# actual decision into policy.py — criteria.md is now passed to evaluate_job and
# ignored. So fixing a policy bug used to leave every previously-rejected job
# suppressed forever, and the documented escape hatch was to edit criteria.md for
# a change that wasn't in criteria.md. Bumping this reopens those jobs instead.
#
# 1 → 2026-07-25: baseline, recorded when the policy became part of the signature.
POLICY_VERSION = "1"


def criteria_version(criteria: str) -> str:
    """Return a short, whitespace-insensitive fingerprint of the fit criteria.

    Includes POLICY_VERSION, because the deterministic policy is as much "the
    criteria" as criteria.md is — it is the half that actually decides.
    """
    return _short_hash(collapse_ws(criteria), POLICY_VERSION)


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
    for marker in _markers_for_tokens(seen, tokens):
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
    """Returns the seen keys as a :class:`SeenSet`, or None on first run.

    None (not an empty set) is the documented first-run sentinel main uses to
    silence jobs older than 7 days.
    """
    if not os.path.exists(SEEN_JOBS_FILE):
        return None
    with open(SEEN_JOBS_FILE) as f:
        return SeenSet(json.load(f))


def state_size_summary(seen) -> str:
    """One line describing how big the dedup state has grown.

    Deliberately reported rather than pruned: the eval:* markers ARE the reopen
    history that order 4d introduced, so dropping old ones would undo that
    remediation. Logging the numbers every run means a future pruning decision
    can rest on measurements instead of a guess.
    """
    try:
        size = os.path.getsize(SEEN_JOBS_FILE)
        size_note = f", {size / 1024:.0f} KB on disk"
    except OSError:
        size_note = ""
    markers = sum(
        1 for key in seen if isinstance(key, str) and key.split(":", 1)[0] in _MARKER_NAMESPACES
    )
    return (
        f"[state] {len(seen)} seen key(s) — {len(seen) - markers} identities, "
        f"{markers} markers{size_note}."
    )


def dump_keys(path: str, keys) -> None:
    """Write ``keys`` to ``path`` atomically in the canonical seen_jobs format.

    Canonical means sorted with ``indent=2`` — the state-branch union merge and
    ``git_sync`` both parse this file, and ``git_sync`` diffs it, so the byte
    layout is part of the contract.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".seen_jobs.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(sorted(keys), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Leave the previous file intact and take the partial temp file with us.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_seen_jobs(seen: set) -> None:
    """Persist the seen set atomically (temp file + rename).

    This file is the system's only dedup memory, and it is rewritten many times
    per run. A plain ``open(..., "w")`` truncates first, so a SIGKILL, an
    OOM-kill (which the Pi troubleshooting table lists as a real occurrence) or a
    power cut mid-write would leave truncated JSON that makes every subsequent
    ``load_seen_jobs`` raise. Writing beside the target and renaming makes the
    replacement atomic on POSIX: a reader sees either the old file or the new
    one. Same tmp-then-rename shape as ``bot.poller.OffsetStore.save`` and
    ``scripts/run_pipeline.sh``'s ``write_last_run``.

    The temp file is created in the target's own directory so the rename stays
    within one filesystem; the on-disk format (sorted, ``indent=2``) is unchanged
    because the state-branch union merge parses it.
    """
    dump_keys(SEEN_JOBS_FILE, seen)
