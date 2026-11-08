"""Convert the engine's seen_jobs.json into the Job Hunter Desktop history format.

    python scripts/convert_history.py --seen state/seen_jobs.json --out history.json

The output is {"format": "jobhunter-history", "version": 1, "seen": [...]}: every job identity
the engine recorded (normalized URLs and "title|company|location" keys), sorted, without the
"eval:", "delivery:" and "deferred:" markers. The app skips every job in it. Only counts are printed.
"""

import argparse
import json
import sys
from pathlib import Path

MARKER_PREFIXES = ("eval:", "delivery:", "deferred:")


def convert(entries) -> list:
    return sorted({e for e in entries if isinstance(e, str) and e and not e.startswith(MARKER_PREFIXES)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seen", type=Path, required=True, help="the engine's seen_jobs.json")
    parser.add_argument("--out", type=Path, required=True, help="the history file to write")
    args = parser.parse_args(argv)
    try:
        entries = json.loads(args.seen.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: cannot read {args.seen}: {type(error).__name__}", file=sys.stderr)
        return 1
    if not isinstance(entries, list):
        print(f"error: {args.seen} is not a JSON list", file=sys.stderr)
        return 1
    seen = convert(entries)
    history = {"format": "jobhunter-history", "version": 1, "seen": seen}
    args.out.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(entries)} entries read, {len(seen)} jobs written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
