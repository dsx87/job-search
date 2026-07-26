"""Sync the dedup state (seen_jobs.json) with the repo's orphan ``state`` branch.

A faithful Python port of the Pi's ``run.sh`` sync_pull / sync_push: PULL the
shared state keys before a run and PUSH them back after, so staggered runners
(the Pi and GitHub Actions) share ordinary seen identities plus namespaced
deferral markers. This prevents both repeated delivery and duplicate deferral
notices. Gated by ``STATE_SYNC=1`` (``PipelineConfig.state_sync``).

Everything is best-effort and **never raises**: a missing ``.state`` checkout,
an unreachable remote, or any git failure logs a ``[state]`` line and returns,
leaving the run to proceed on the local seen_jobs.json. All git runs via
``git -C <state_dir>`` (no chdir), so this is safe to call from the pipeline.
"""
import json
import os
import shutil
import subprocess
import sys

from ..config import SEEN_JOBS_FILE
from .seen_jobs import dump_keys
from .seen_merge import merge_refs

STATE_DIR = ".state"          # dedicated checkout of the `state` branch
STATE_BRANCH = "state"
COMMIT_MESSAGE = "chore: update seen jobs state (rpi-igor) [skip ci]"
PUSH_ATTEMPTS = 5


def _run(state_dir, *args):
    """Run ``git -C state_dir <args>`` capturing output. May raise only if the
    git binary is missing — callers wrap this in try/except to stay non-raising."""
    return subprocess.run(["git", "-C", state_dir, *args], capture_output=True, text=True)


def _count(path):
    """Key count in a seen_jobs.json file for logging; '?' if unreadable."""
    try:
        with open(path) as f:
            return len(json.load(f))
    except Exception:
        return "?"


def _keys_from_file(path):
    """Keys in a seen_jobs.json file, or an empty set when absent/unreadable."""
    try:
        with open(path) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def pull_state(state_dir=STATE_DIR, branch=STATE_BRANCH, seen_file=SEEN_JOBS_FILE) -> bool:
    """Merge origin/<branch>'s keys INTO seen_file. Returns True on success.

    A union, not an overwrite. push_state promises that a failed push leaves the
    state local and "will retry next run" — but this ran first next run, and
    before 2026-07-25 it was ``reset --hard`` + ``copyfile``, which deleted
    exactly the keys that were waiting to be retried (finding N2). Losing them
    means re-notified jobs, duplicate CVs, and a reset retry ladder. Set-union is
    the same semantics push_state already uses on a rejected push, so the two
    directions now agree.

    Missing checkout / offline remote / any git error → log a skip and return
    False, leaving seen_file untouched so the run uses the local baseline."""
    if not os.path.isdir(os.path.join(state_dir, ".git")):
        print(f"[state] pull skipped (no {state_dir} checkout); using local {seen_file} "
              f"({_count(seen_file)} keys)", file=sys.stderr)
        return False
    local = _keys_from_file(seen_file)
    try:
        if _run(state_dir, "fetch", "--quiet", "--depth", "1", "origin", branch).returncode != 0:
            raise RuntimeError("fetch failed")
        if _run(state_dir, "reset", "--hard", "--quiet", f"origin/{branch}").returncode != 0:
            raise RuntimeError("reset failed")
        remote_path = os.path.join(state_dir, seen_file)
        with open(remote_path) as f:
            remote = set(json.load(f))
        merged = local | remote
        # Only rewrite when the union actually differs from what is on disk, so a
        # no-op pull can't disturb the file the run is about to read.
        if merged != local:
            dump_keys(seen_file, merged)
    except Exception:
        print(f"[state] pull skipped (no remote/key or offline); using local {seen_file} "
              f"({_count(seen_file)} keys)", file=sys.stderr)
        return False
    local_only = len(local - remote)
    detail = f" (kept {local_only} local-only key(s))" if local_only else ""
    print(f"[state] pulled {len(remote)} keys from origin/{branch}; "
          f"{_count(seen_file)} after union{detail}")
    return True


def push_state(state_dir=STATE_DIR, branch=STATE_BRANCH, seen_file=SEEN_JOBS_FILE,
               message=COMMIT_MESSAGE, attempts=PUSH_ATTEMPTS) -> bool:
    """Push the local seen_file to origin/<branch>, union-merging on rejection.

    Safe to call even after a non-zero pipeline exit: ordinary keys record
    handled or deliberately silenced job identities, while reserved
    ``deferred:`` markers suppress repeat notices without marking jobs seen.
    Both are string keys with set-union semantics, so partial-run state is safe
    to share. Returns True when the remote ends up holding our keys (pushed /
    already had them / nothing changed), False when we could not get them
    there. Never raises."""
    if not os.path.isdir(os.path.join(state_dir, ".git")):
        print(f"[state] push skipped (no {state_dir} checkout)", file=sys.stderr)
        return False
    dst = os.path.join(state_dir, seen_file)
    try:
        shutil.copyfile(seen_file, dst)
        _run(state_dir, "add", seen_file)
        if _run(state_dir, "diff", "--cached", "--quiet").returncode == 0:
            print("[state] no changes to push")
            return True
        # Commit identity comes from the checkout's local git config (set by
        # scripts/setup-state-sync.sh); a missing identity fails the commit.
        if _run(state_dir, "commit", "--quiet", "-m", message).returncode != 0:
            print("[state] WARNING: commit failed (no git identity?); state left local",
                  file=sys.stderr)
            return False

        # Push with retry: a concurrent push to `state` is rejected. Rather than a
        # textual rebase (which can corrupt the JSON array), rebuild seen_jobs.json
        # as the set-union of our keys and the remote's, recommit on the remote
        # tip, and retry.
        for attempt in range(1, attempts + 1):
            if _run(state_dir, "push", "--quiet", "origin", f"HEAD:{branch}").returncode == 0:
                print(f"[state] pushed {_count(dst)} keys on attempt {attempt}")
                return True
            print(f"[state] push rejected (attempt {attempt}); union-merging remote and retrying...",
                  file=sys.stderr)
            if _run(state_dir, "fetch", "--quiet", "--depth", "1", "origin", branch).returncode != 0:
                break
            # Union our commit (HEAD) with the freshly-fetched remote tip BEFORE
            # resetting HEAD onto it. merge_refs reads each ref via `git show`.
            merged = merge_refs(["HEAD", f"origin/{branch}"], filename=seen_file, repo_dir=state_dir)
            _run(state_dir, "reset", "--hard", "--quiet", f"origin/{branch}")
            dump_keys(dst, merged)  # byte-identical to save_seen_jobs format
            _run(state_dir, "add", seen_file)
            if _run(state_dir, "diff", "--cached", "--quiet").returncode == 0:
                print("[state] remote already has all our keys; nothing to push")
                return True
            if _run(state_dir, "commit", "--quiet", "-m", message).returncode != 0:
                print("[state] WARNING: recommit failed; state left local", file=sys.stderr)
                break
        print("[state] WARNING: could not push seen_jobs.json; state left local, will retry next run",
              file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[state] WARNING: push failed unexpectedly ({exc}); state left local", file=sys.stderr)
        return False
