"""Tests for the state git-sync (pull/push around run_daily).

Uses real throwaway git repos with ``file://`` remotes so the round-trip is
exercised for real yet stays offline and deterministic (git is present on the
tests.yml runner). Global/system git config is nulled out so a developer's
settings can't leak in; each checkout gets a local identity, matching what
scripts/setup-state-sync.sh does on the Pi.
"""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from job_search.state import git_sync


def _git(cwd, *args):
    """Run a git command for fixture setup; fail the test loudly on error."""
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _set_identity(repo):
    _git(repo, "config", "user.email", "runner@example.com")
    _git(repo, "config", "user.name", "Test Runner")


def _write_seen(path, keys):
    """Write seen keys in the canonical (sorted, indent=2) seen_jobs.json format."""
    with open(path, "w") as f:
        json.dump(sorted(keys), f, indent=2)


def _read_seen(path):
    with open(path) as f:
        return set(json.load(f))


def _origin_keys(env):
    r = subprocess.run(
        ["git", "-C", str(env.origin), "show", "state:seen_jobs.json"],
        capture_output=True, text=True, check=True,
    )
    return set(json.loads(r.stdout))


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    # Isolate from the developer's global/system git config.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    origin_url = "file://" + str(origin)

    # Seed origin/state with initial keys {a, b} via a throwaway clone.
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", origin_url, str(seed))
    _set_identity(seed)
    _git(seed, "checkout", "-b", "state")
    _write_seen(seed / "seen_jobs.json", {"a", "b"})
    _git(seed, "add", "seen_jobs.json")
    _git(seed, "commit", "-m", "seed state")
    _git(seed, "push", "origin", "state")

    # The runner's dedicated .state checkout (what git_sync operates on), plus a
    # working dir that holds the "repo root" seen_jobs.json (opened by bare
    # relative path, exactly as in production).
    work = tmp_path / "work"
    work.mkdir()
    state_dir = work / ".state"
    _git(work, "clone", "--branch", "state", origin_url, str(state_dir))
    _set_identity(state_dir)

    monkeypatch.chdir(work)
    return SimpleNamespace(
        tmp_path=tmp_path, origin=origin, origin_url=origin_url, work=work, state_dir=state_dir
    )


def _push_concurrent(env, keys, msg="concurrent update"):
    """Simulate the other runner: clone, set keys, push to origin/state."""
    other = env.tmp_path / "other"
    _git(env.tmp_path, "clone", "--branch", "state", env.origin_url, str(other))
    _set_identity(other)
    _write_seen(other / "seen_jobs.json", keys)
    _git(other, "add", "seen_jobs.json")
    _git(other, "commit", "-m", msg)
    _git(other, "push", "origin", "state")


# ── pull ──────────────────────────────────────────────────────────────────────
def test_pull_state_no_checkout_returns_false(state_env, capsys):
    assert git_sync.pull_state(state_dir="nonexistent") is False
    assert "pull skipped" in capsys.readouterr().err


def test_pull_state_refreshes_local(state_env):
    # A concurrent runner advances origin/state to include 'e'.
    _push_concurrent(state_env, {"a", "b", "e"})
    assert git_sync.pull_state(state_dir=".state") is True
    assert _read_seen("seen_jobs.json") == {"a", "b", "e"}


# ── push ──────────────────────────────────────────────────────────────────────
def test_push_state_no_checkout_returns_false(state_env, capsys):
    assert git_sync.push_state(state_dir="nonexistent") is False
    assert "push skipped" in capsys.readouterr().err


def test_push_state_no_changes_is_noop(state_env, capsys):
    _write_seen("seen_jobs.json", {"a", "b"})  # identical to what .state holds
    assert git_sync.push_state(state_dir=".state") is True
    assert "no changes to push" in capsys.readouterr().out
    assert _origin_keys(state_env) == {"a", "b"}


def test_push_state_happy_path_exact_commit_message(state_env):
    _write_seen("seen_jobs.json", {"a", "b", "d"})
    assert git_sync.push_state(state_dir=".state") is True
    assert _origin_keys(state_env) == {"a", "b", "d"}
    r = subprocess.run(
        ["git", "-C", str(state_env.origin), "log", "-1", "--format=%s", "state"],
        capture_output=True, text=True, check=True,
    )
    assert r.stdout.strip() == git_sync.COMMIT_MESSAGE


def test_push_state_union_merges_on_race(state_env):
    # Our run produced {a,b,d}; meanwhile a concurrent runner pushed {a,b,c}.
    _write_seen("seen_jobs.json", {"a", "b", "d"})
    _push_concurrent(state_env, {"a", "b", "c"})
    assert git_sync.push_state(state_dir=".state") is True
    # First push is rejected, remote union-merged, retry succeeds → sorted union.
    assert _origin_keys(state_env) == {"a", "b", "c", "d"}
