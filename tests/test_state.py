"""Characterization tests for seen-jobs state and JobStore.

The seen_jobs.json format (sorted list, indent=2) is load-bearing: the daily
workflow's set-union merge depends on it byte-for-byte.
"""
import json
import datetime
import hashlib

import pytest

# --- modules under test (repoint on migration) ---
from job_search.state import seen_jobs as seen_mod
from job_search.state import seen_merge
from job_search.state.seen_jobs import (
    LifecycleRecord,
    acknowledge_block_alert,
    criteria_version,
    delivery_identity_tokens,
    delivery_retry_state,
    dump_keys,
    evaluation_signature,
    lifecycle_record,
    load_seen_jobs,
    mark_delivery_notified,
    normalize_url,
    pending_block_alerts,
    record_delivery_failure,
    record_evaluation,
    save_seen_jobs,
    should_reevaluate,
    title_company_key,
)
from job_search.state.seen_merge import keys_from_ref, merge_refs, write_merged
from job_search.identity import job_identity_keys
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


def test_save_seen_jobs_is_atomic_and_leaves_no_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "seen.json"
    monkeypatch.setattr(seen_mod, "SEEN_JOBS_FILE", str(path))
    save_seen_jobs({"a"})
    save_seen_jobs({"a", "b"})
    assert load_seen_jobs() == {"a", "b"}
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]


def test_save_seen_jobs_survives_a_torn_write(tmp_path, monkeypatch):
    # finding N4: `open(..., "w")` truncates first, so a SIGKILL / OOM-kill /
    # power cut mid-write left truncated JSON and every later run raised
    # JSONDecodeError on the only dedup memory the system has. With tmp+rename,
    # a crashed write leaves the previous file exactly as it was.
    path = tmp_path / "seen.json"
    monkeypatch.setattr(seen_mod, "SEEN_JOBS_FILE", str(path))
    save_seen_jobs({"first", "second"})
    before = path.read_text()

    def _die(*_args, **_kwargs):
        raise KeyboardInterrupt("killed mid-write")

    monkeypatch.setattr(seen_mod.json, "dump", _die)
    with pytest.raises(KeyboardInterrupt):
        save_seen_jobs({"third"})

    assert path.read_text() == before
    assert load_seen_jobs() == {"first", "second"}
    # ...and the partial temp file is not left behind to accumulate.
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]


def test_dump_keys_writes_the_canonical_format_to_any_path(tmp_path):
    target = tmp_path / "elsewhere.json"
    dump_keys(str(target), {"b", "a"})
    assert target.read_text() == json.dumps(["a", "b"], indent=2)


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


def test_jobstore_merge_retains_missing_jobs_from_incomplete_sources(tmp_path):
    store = JobStore(path=str(tmp_path / "store.json"))
    store.merge([
        Job(title="Keep", url="u1", source="partial"),
        Job(title="Drop", url="u2", source="healthy"),
    ])

    store.merge([], incomplete_sources={"partial"})

    assert set(store.jobs) == {"u1"}
    assert store.jobs["u1"]["source"] == "partial"


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


def test_jobstore_keeps_distinct_url_backed_postings_with_same_fallback(tmp_path):
    store = JobStore(path=str(tmp_path / "store.json"))
    first = Job(title="iOS Engineer", company="Acme", url="https://x/1")
    second = Job(
        title="iOS Engineer",
        company="Acme",
        url="https://x/2",
        description="new opening",
    )
    store.merge([first])
    assert store.toggle_seen(first) is True

    store.merge([first, second])

    assert set(store.jobs) == {"https://x/1", "https://x/2"}
    assert store.jobs["https://x/1"]["seen"] is True
    assert store.jobs["https://x/1"]["url"] == "https://x/1"
    assert store.jobs["https://x/2"]["seen"] is False
    assert store.jobs["https://x/2"]["url"] == "https://x/2"
    assert store.jobs["https://x/2"]["description"] == "new opening"


def test_jobstore_toggles_legacy_raw_url_key_from_stored_job(tmp_path):
    path = tmp_path / "store.json"
    raw_url = "https://X.com/Role/"
    path.write_text(json.dumps({
        "jobs": {
            raw_url: {
                "title": "iOS",
                "company": "Acme",
                "url": raw_url,
                "seen": False,
            }
        }
    }))
    store = JobStore(path=str(path))

    assert store.toggle_seen(store.jobs[raw_url]) is True
    assert store.jobs[raw_url]["seen"] is True


def test_jobstore_merge_builds_stored_identity_index_once(tmp_path, monkeypatch):
    store = JobStore(path=str(tmp_path / "store.json"))
    store.jobs = {
        "https://old/{}".format(index): Job(
            title="Old {}".format(index), url="https://old/{}".format(index)
        ).to_dict()
        for index in range(10)
    }
    new_jobs = [
        Job(title="New {}".format(index), url="https://new/{}".format(index))
        for index in range(10)
    ]
    original = Job.from_dict
    calls = []

    def counted(data):
        calls.append(data)
        return original(data)

    monkeypatch.setattr(Job, "from_dict", counted)

    store.merge(new_jobs)

    assert len(calls) <= 20


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


# ── audit order 4d: structured lifecycle seen-state ───────────────────────────

def _is_short_hex(token):
    """A 16-char lowercase-hex token, as criteria_version/evaluation_signature emit."""
    return (
        isinstance(token, str)
        and len(token) == 16
        and set(token) <= set("0123456789abcdef")
    )


def test_criteria_version_is_deterministic_whitespace_insensitive_and_short_hex():
    token = criteria_version("a  b")
    assert token == criteria_version("a  b")  # deterministic
    assert token == criteria_version(" a b ")  # whitespace-insensitive
    assert _is_short_hex(token)


def test_criteria_version_differs_on_change():
    assert criteria_version("remote only") != criteria_version("onsite only")


def test_evaluation_signature_is_deterministic_short_hex_and_colon_free():
    sig = evaluation_signature("Some job content", "cv1")
    assert sig == evaluation_signature("Some job content", "cv1")  # deterministic
    assert _is_short_hex(sig)
    assert ":" not in sig


def test_evaluation_signature_is_whitespace_insensitive_on_content():
    assert evaluation_signature("a  b", "cv1") == evaluation_signature(" a b ", "cv1")


def test_evaluation_signature_differs_on_content_and_on_criteria_version():
    base = evaluation_signature("content one", "cv1")
    assert base != evaluation_signature("content two", "cv1")  # content change
    assert base != evaluation_signature("content one", "cv2")  # criteria change


def test_evaluation_signature_empty_content_is_stable_and_nonempty():
    empty = evaluation_signature("", "")
    assert empty == evaluation_signature("", "")
    assert empty  # non-empty, stable
    assert _is_short_hex(empty)


def test_evaluation_signature_ignores_digit_churn_but_not_textual_change():
    # Cosmetic numeric churn (applicant counts, "reposted N days ago", version
    # bumps) must NOT flip the signature, else a listed job re-evaluates forever.
    base = evaluation_signature("Senior iOS role. Over 214 applicants. Reposted 3 days ago.", "cv1")
    churned = evaluation_signature("Senior iOS role. Over 512 applicants. Reposted 9 days ago.", "cv1")
    assert base == churned
    # A substantive textual correction still changes the signature.
    corrected = evaluation_signature("Senior iOS role. US residents only. Over 214 applicants.", "cv1")
    assert base != corrected


def test_record_evaluation_and_lifecycle_record_round_trip_across_both_identities():
    seen = set()
    job = Job(title="iOS Dev", company="Acme", url="https://x/role", location="Tel Aviv")
    sig = evaluation_signature("cleaned description", criteria_version("criteria"))
    today = datetime.date(2026, 7, 16)

    record_evaluation(seen, job, sig, "nonfit", today)

    tokens = delivery_identity_tokens("https://x/role", "iOS Dev", "Acme", "Tel Aviv")
    assert len(tokens) == 2  # a URL identity and a title/company/location identity
    for token in tokens:
        assert f"eval:sig:{token}:{sig}" in seen
        assert f"eval:verdict:{token}:{sig}:nonfit" in seen

    by_url = lifecycle_record(seen, url="https://x/role")
    by_key = lifecycle_record(seen, title="iOS Dev", company="Acme", location="Tel Aviv")
    for record in (by_url, by_key):
        assert record.known is True
        assert sig in record.signatures
        assert "nonfit" in record.verdicts
        assert record.first_seen == today
        assert record.last_seen == today


def test_record_evaluation_is_a_noop_for_identityless_job():
    seen = set()
    sig = evaluation_signature("x", "")
    today = datetime.date(2026, 7, 16)

    record_evaluation(seen, Job(), sig, "nonfit", today)
    assert seen == set()

    record_evaluation(
        seen,
        {"url": "", "title": "", "company": "", "location": ""},
        sig,
        "nonfit",
        today,
    )
    assert seen == set()


def test_lifecycle_record_empty_when_no_markers():
    record = lifecycle_record(set(), url="https://x/role")
    assert record.known is False
    assert record.signatures == frozenset()
    assert record.verdicts == frozenset()
    assert record.first_seen is None
    assert record.last_seen is None


def test_lifecycle_record_first_is_min_last_is_max_ignoring_bad_dates():
    token = delivery_identity_tokens("https://x/role")[0]
    sig = evaluation_signature("anything")
    seen = {
        f"eval:sig:{token}:{sig}",
        f"eval:first:{token}:2026-07-10",
        f"eval:first:{token}:2026-07-05",
        f"eval:first:{token}:2026-07-20",
        f"eval:last:{token}:2026-07-10",
        f"eval:last:{token}:2026-07-25",
        f"eval:last:{token}:2026-07-01",
        f"eval:first:{token}:not-a-date",  # malformed date, ignored
        f"eval:last:{token}:2026-13-99",  # malformed date, ignored
    }

    record = lifecycle_record(seen, url="https://x/role")

    assert record.first_seen == datetime.date(2026, 7, 5)
    assert record.last_seen == datetime.date(2026, 7, 25)


def test_lifecycle_record_ignores_noise_other_tokens_and_invalid_verdicts():
    tokens = delivery_identity_tokens("https://x/role", "iOS", "Acme", "")
    url_token = tokens[0]
    other_token = delivery_identity_tokens("https://other/role")[0]
    sig = evaluation_signature("content", criteria_version("crit"))
    seen = {
        12345,  # non-string member (union merges can carry anything)
        None,  # non-string member
        "https://x/role",  # plain identity string, not an eval marker
        "ios|acme",  # plain identity string
        f"delivery:notified:{url_token}",  # unrelated delivery marker
        f"eval:sig:{url_token}:{sig}",  # valid, this identity
        f"eval:verdict:{url_token}:{sig}:nonfit",  # valid verdict word
        f"eval:verdict:{url_token}:{sig}:maybe",  # invalid verdict word -> ignored
        f"eval:sig:{other_token}:{sig}",  # different token -> ignored
        f"eval:verdict:{other_token}:{sig}:fit",  # different token -> ignored
        "eval:garbage",  # malformed marker -> ignored
    }

    record = lifecycle_record(seen, url="https://x/role", title="iOS", company="Acme")

    assert record.known is True
    assert record.signatures == frozenset({sig})
    assert record.verdicts == frozenset({"nonfit"})


def test_should_reevaluate_truth_table():
    job = Job(title="Match", company="Acme", url="https://x/match")
    crit = criteria_version("criteria")
    sig_a = evaluation_signature("content A", crit)
    sig_b = evaluation_signature("content B", crit)
    today = datetime.date(2026, 7, 16)

    # empty seen -> never force a reopen
    assert should_reevaluate(set(), job, sig_a) is False

    # legacy: only plain identity strings, no eval markers -> never force a reopen
    legacy = set(job_identity_keys(job))
    assert should_reevaluate(legacy, job, sig_a) is False

    # recorded non-fit, SAME signature -> already evaluated this content+criteria
    nonfit = set()
    record_evaluation(nonfit, job, sig_a, "nonfit", today)
    assert should_reevaluate(nonfit, job, sig_a) is False
    # recorded non-fit, DIFFERENT signature -> reopen
    assert should_reevaluate(nonfit, job, sig_b) is True

    # recorded fit, DIFFERENT signature -> never re-open a delivered fit
    fit = set()
    record_evaluation(fit, job, sig_a, "fit", today)
    assert should_reevaluate(fit, job, sig_b) is False

    # recorded deferred, DIFFERENT signature -> reopen
    deferred = set()
    record_evaluation(deferred, job, sig_a, "deferred", today)
    assert should_reevaluate(deferred, job, sig_b) is True


def test_seen_jobs_roundtrip_preserves_eval_markers_as_sorted_list(tmp_path, monkeypatch):
    path = tmp_path / "seen.json"
    monkeypatch.setattr(seen_mod, "SEEN_JOBS_FILE", str(path))
    job = Job(title="Match", company="Acme", url="https://x/match")
    seen = set(job_identity_keys(job))
    record_evaluation(
        seen,
        job,
        evaluation_signature("desc", criteria_version("criteria")),
        "nonfit",
        datetime.date(2026, 7, 16),
    )

    save_seen_jobs(seen)

    on_disk = json.loads(path.read_text())
    assert isinstance(on_disk, list)
    assert on_disk == sorted(on_disk)  # sorted JSON list of strings, as the merge relies on
    assert all(isinstance(item, str) for item in on_disk)
    assert any(item.startswith("eval:") for item in on_disk)
    assert load_seen_jobs() == seen  # exact round-trip


def test_eval_markers_do_not_corrupt_delivery_state_or_identities():
    job = Job(title="Match", company="Acme", url="https://x/match", location="EU")
    job_dict = {"url": job.url, "title": job.title, "company": job.company, "location": job.location}
    today = datetime.date(2026, 7, 16)

    # delivery state built WITHOUT any eval markers, as a baseline
    baseline = set()
    mark_delivery_notified(baseline, **job_dict)
    record_delivery_failure(baseline, job_dict, today, "document")
    baseline_state = delivery_retry_state(baseline, **job_dict)

    # same delivery calls, now interleaved with eval markers for the same job
    seen = set()
    sig = evaluation_signature("desc", criteria_version("criteria"))
    record_evaluation(seen, job, sig, "nonfit", today)
    mark_delivery_notified(seen, **job_dict)
    record_delivery_failure(seen, job_dict, today, "document")
    record_evaluation(seen, job, sig, "fit", today)

    # eval markers must not perturb delivery parsing
    assert delivery_retry_state(seen, **job_dict) == baseline_state
    assert pending_block_alerts(seen) == pending_block_alerts(baseline)

    # eval:* markers must never act as seen identities
    identities = set(job_identity_keys(job))
    assert not any(marker.startswith("eval:") for marker in identities)
    assert any(isinstance(marker, str) and marker.startswith("eval:") for marker in seen)
