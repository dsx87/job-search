"""Set-union merge of seen_jobs.json across git refs (daily-workflow helper).

When the daily run's push to the orphan `state` branch is rejected (a
concurrent run pushed first), a textual rebase could corrupt the JSON array, so
we instead rebuild seen_jobs.json as the set-union of our keys and the remote's.
This is the logic that used to live as an inline Python heredoc in the workflow.

Run from the state-branch checkout (so the refs resolve there), with the package
importable via PYTHONPATH:

    PYTHONPATH="$GITHUB_WORKSPACE" \
      python3 -m job_search.state.seen_merge /tmp/seen_union.json HEAD origin/state
"""
import json
import subprocess
import sys

from ..config import SEEN_JOBS_FILE


def keys_from_ref(ref, filename=SEEN_JOBS_FILE):
    """The set of seen-keys recorded at `ref:filename`, or empty if absent/blank."""
    r = subprocess.run(
        ["git", "show", f"{ref}:{filename}"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return set()
    return set(json.loads(r.stdout))


def merge_refs(refs, filename=SEEN_JOBS_FILE):
    """Sorted set-union of the seen-keys across `refs`."""
    merged = set()
    for ref in refs:
        merged |= keys_from_ref(ref, filename)
    return sorted(merged)


def write_merged(out_path, refs, filename=SEEN_JOBS_FILE):
    """Write the sorted union to out_path in the canonical seen_jobs.json format."""
    merged = merge_refs(refs, filename)
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=2)
    return merged


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(
            "usage: python -m job_search.state.seen_merge <out_path> <ref> [<ref> ...]",
            file=sys.stderr,
        )
        return 2
    out_path, refs = argv[0], argv[1:]
    merged = write_merged(out_path, refs)
    print(f"Merged {len(refs)} ref(s) into {len(merged)} seen key(s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
