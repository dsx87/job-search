"""Characterization tests for seen-jobs state and JobStore.

The seen_jobs.json format (sorted list, indent=2) is load-bearing: the daily
workflow's set-union merge depends on it byte-for-byte.
"""
import json

# --- modules under test (repoint on migration) ---
from job_search.state import seen_jobs as seen_mod
from job_search.state import seen_merge
from job_search.state.seen_jobs import normalize_url, title_company_key, load_seen_jobs, save_seen_jobs
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


def test_job_to_store_dict():
    d = job_to_store_dict(Job(title="iOS", url="u1", region=Region.EU))
    assert d["seen"] is False
    assert d["region"] == "EU"
    assert d["url"] == "u1"


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
