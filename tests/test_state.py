"""Characterization tests for seen-jobs state and JobStore.

The seen_jobs.json format (sorted list, indent=2) is load-bearing: the daily
workflow's set-union merge depends on it byte-for-byte.
"""
import json
import datetime
import hashlib

# --- modules under test (repoint on migration) ---
from job_search.state import seen_jobs as seen_mod
from job_search.state import seen_merge
from job_search.state.seen_jobs import (
    acknowledge_block_alert,
    delivery_identity_tokens,
    delivery_retry_state,
    load_seen_jobs,
    mark_delivery_notified,
    normalize_url,
    pending_block_alerts,
    record_delivery_failure,
    save_seen_jobs,
    title_company_key,
)
from job_search.state.seen_merge import keys_from_ref, merge_refs, write_merged
from job_search.models import Job, Region
from job_search.state.job_store import JobStore, job_to_store_dict


def test_normalize_url():
    assert normalize_url("HTTPS://X.com/A/") == "https://x.com/a"
    assert normalize_url("https://x.com/a") == "https://x.com/a"


def test_title_company_key():
    assert title_company_key("iOS Dev", "Acme", "Tel Aviv ") == "ios dev|acme|tel aviv"
    assert title_company_key("iOS Dev", "Acme") == "ios dev|acme"


def test_load_seen_jobs_none_sentinel_on_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(seen_mod, "SEEN_JOBS_FILE", str(tmp_path / "absent.json"))
    assert load_seen_jobs() is None  # first-run sentinel


def test_seen_jobs_roundtrip_and_format(tmp_path, monkeypatch):
    path = tmp_path / "seen.json"
    monkeypatch.setattr(seen_mod, "SEEN_JOBS_FILE", str(path))
    save_seen_jobs({"b", "a", "c"})
    # exact on-disk format the workflow merge relies on
    assert path.read_text() == json.dumps(["a", "b", "c"], indent=2)
    assert load_seen_jobs() == {"a", "b", "c"}


def test_delivery_identity_tokens_use_full_sha256_for_url_and_meaningful_job_key():
    tokens = delivery_identity_tokens("HTTPS://X.com/Role/", " iOS Dev ", "Acme", " Tel Aviv ")

    assert tokens == (
        hashlib.sha256(b"https://x.com/role").hexdigest(),
        hashlib.sha256(b"ios dev|acme|tel aviv").hexdigest(),
    )
    assert delivery_identity_tokens("", "", "", "") == ()


def test_delivery_attempt_markers_round_trip_across_both_identities():
    seen = set()
    job = {"url": "https://x/role", "title": "iOS", "company": "Acme", "location": "EU"}

    state = record_delivery_failure(seen, job, datetime.date(2026, 7, 15), "preparation")

    assert state.attempt == 1
    assert state.retry_on == datetime.date(2026, 7, 16)
    assert state.blocked is False
    tokens = delivery_identity_tokens("https://x/role", "iOS", "Acme", "EU")
    assert all(f"delivery:attempt:{token}:1:2026-07-16" in seen for token in tokens)
    assert delivery_retry_state(seen, **job) == state


def test_delivery_retry_state_uses_highest_union_attempt_and_latest_date():
    token = delivery_identity_tokens("https://x/role", "", "", "")[0]
    seen = {
        f"delivery:attempt:{token}:1:2026-07-16",
        f"delivery:attempt:{token}:2:2026-07-17",
        f"delivery:attempt:{token}:2:2026-07-18",
        "delivery:attempt:bad:wat:nope",
        f"delivery:attempt:{token}:99:2026-07-19",
        "delivery:unknown:anything",
    }

    state = delivery_retry_state(seen, url="https://x/role")

    assert state.attempt == 2
    assert state.retry_on == datetime.date(2026, 7, 18)


def test_notification_and_blocked_state_are_union_safe_for_dual_identity():
    seen = set()
    job = {"url": "https://x/role", "title": "iOS", "company": "Acme", "location": "EU"}
    mark_delivery_notified(seen, **job)
    record_delivery_failure(seen, job, datetime.date(2026, 7, 15), "document")
    record_delivery_failure(seen, job, datetime.date(2026, 7, 16), "document")
    state = record_delivery_failure(seen, job, datetime.date(2026, 7, 18), "document")

    assert state.attempt == 3
    assert state.notified is True
    assert state.blocked is True
    assert state.retry_on is None
    tokens = delivery_identity_tokens(**job)
    assert all(f"delivery:notified:{token}" in seen for token in tokens)
    assert all(f"delivery:blocked:{token}" in seen for token in tokens)


def test_pending_block_alert_round_trip_and_acknowledgement():
    seen = set()
    job = {
        "url": "https://x/role",
        "title": "<iOS>" + "x" * 300,
        "company": "Acme",
        "location": "EU",
    }
    record_delivery_failure(seen, job, datetime.date(2026, 7, 15), "preparation")
    record_delivery_failure(seen, job, datetime.date(2026, 7, 16), "preparation")
    record_delivery_failure(seen, job, datetime.date(2026, 7, 18), "preparation")

    alerts = pending_block_alerts(seen)

    assert len(alerts) == 1
    token, payload = alerts[0]
    assert payload["url"] == "https://x/role"
    assert payload["stage"] == "preparation"
    assert len(payload["title"]) <= 180
    acknowledge_block_alert(seen, token)
    assert pending_block_alerts(seen) == []
    assert f"delivery:block-alerted:{token}" in seen


def test_pending_block_alerts_ignore_malformed_payloads():
    token = "a" * 64

    assert pending_block_alerts({f"delivery:block-alert:{token}:a"}) == []


def test_job_to_store_dict():
    d = job_to_store_dict(Job(title="iOS", url="u1", region=Region.EU, description="Full text"))
    assert d["seen"] is False
    assert d["region"] == "EU"
    assert d["url"] == "u1"
    assert d["description"] == "Full text"


def test_jobstore_loads_old_tui_state_and_retains_seen_status(tmp_path):
    path = tmp_path / "store.json"
    path.write_text(json.dumps({
        "jobs": {
            "u1": {
                "title": "Old job",
                "company": "Acme",
                "url": "u1",
                "region": "EU",
                "seen": True,
            }
        },
        "show_seen": True,
    }))

    store = JobStore(path=str(path))

    assert store.jobs["u1"]["seen"] is True
    assert store.show_seen is True


def test_jobstore_merge_sort_and_toggle(tmp_path):
    path = str(tmp_path / "store.json")
    store = JobStore(path=path)
    store.merge([
        Job(title="B role", url="u1", region=Region.US),
        Job(title="A role", url="u2", region=Region.EU),
    ])
    # sorted by region order (EU before US), then title
    assert [j["title"] for j in store.get_jobs()] == ["A role", "B role"]

    assert store.toggle_seen("u2") is True
    assert [j["title"] for j in store.get_jobs()] == ["B role"]  # seen hidden
    assert store.toggle_show_seen() is True
    assert len(store.get_jobs()) == 2  # now shown

    # merge that drops u1 removes it
    store.merge([Job(title="A role", url="u2", region=Region.EU)])
    assert set(store.jobs.keys()) == {"u2"}


def test_jobstore_persists(tmp_path):
    path = str(tmp_path / "store.json")
    store = JobStore(path=path)
    store.merge([Job(title="X", url="u1", region=Region.EU)])
    reopened = JobStore(path=path)
    assert "u1" in reopened.jobs


def test_jobstore_uses_canonical_keys_for_url_less_and_equivalent_jobs(tmp_path):
    store = JobStore(path=str(tmp_path / "store.json"))
    first = Job(title="iOS", company="Acme")
    second = Job(title="macOS", company="Beta")
    store.merge([first, second])

    assert set(store.jobs) == {"ios|acme", "macos|beta"}
    assert store.toggle_seen(first) is True

    store.merge([Job(title=" IOS ", company="ACME"), second])

    assert store.jobs["ios|acme"]["seen"] is True


# ── seen_merge: the workflow's set-union merge (extracted from inline YAML) ────

class _FakeProc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def _fake_git_show(ref_to_json, monkeypatch):
    """Patch subprocess.run so `git show <ref>:seen_jobs.json` returns canned JSON.
    A ref absent from the mapping mimics a failed `git show` (returncode 1)."""
    def fake_run(cmd, capture_output=True, text=True):
        spec = cmd[2]  # "<ref>:seen_jobs.json"
        ref = spec.split(":", 1)[0]
        if ref in ref_to_json:
            return _FakeProc(0, ref_to_json[ref])
        return _FakeProc(1, "")
    monkeypatch.setattr(seen_merge.subprocess, "run", fake_run)


def test_keys_from_ref_missing_is_empty(monkeypatch):
    _fake_git_show({}, monkeypatch)
    assert keys_from_ref("origin/state") == set()


def test_merge_refs_is_sorted_union(monkeypatch):
    _fake_git_show({
        "HEAD": json.dumps(["b", "a", "c"]),
        "origin/state": json.dumps(["c", "d"]),
    }, monkeypatch)
    assert merge_refs(["HEAD", "origin/state"]) == ["a", "b", "c", "d"]


def test_write_merged_format_matches_seen_jobs(tmp_path, monkeypatch):
    _fake_git_show({
        "HEAD": json.dumps(["b", "a"]),
        "origin/state": json.dumps(["a", "c"]),
    }, monkeypatch)
    out = tmp_path / "seen_union.json"
    merged = write_merged(str(out), ["HEAD", "origin/state"])
    assert merged == ["a", "b", "c"]
    # byte-for-byte the same format save_seen_jobs / the state branch expects
    assert out.read_text() == json.dumps(["a", "b", "c"], indent=2)


def test_keys_from_ref_repo_dir_inserts_dash_C(monkeypatch):
    """With repo_dir set, git runs under `-C <dir>`; unset keeps `git show <spec>`."""
    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        return _FakeProc(0, json.dumps(["k1", "k2"]))

    monkeypatch.setattr(seen_merge.subprocess, "run", fake_run)
    keys = keys_from_ref("HEAD", repo_dir="/tmp/state")
    assert keys == {"k1", "k2"}
    assert captured["cmd"] == ["git", "-C", "/tmp/state", "show", "HEAD:seen_jobs.json"]
