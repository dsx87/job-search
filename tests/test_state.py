"""Characterization tests for seen-jobs state and JobStore.

The seen_jobs.json format (sorted list, indent=2) is load-bearing: the daily
workflow's set-union merge depends on it byte-for-byte.
"""
import json

# --- modules under test (repoint on migration) ---
from job_search.state import seen_jobs as seen_mod
from job_search.state.seen_jobs import normalize_url, title_company_key, load_seen_jobs, save_seen_jobs
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
