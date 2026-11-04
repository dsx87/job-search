"""CLI: ``python -m scripts.parity_export <area> --out <dir>``."""
import argparse
import datetime
import importlib
import platform
import subprocess
import sys
from pathlib import Path

from . import write_canonical

AREAS = {"llm": "scripts.parity_export.llm"}

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _engine_commit() -> str:
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()

    dirty = git("status", "--porcelain", "--untracked-files=no")
    return git("rev-parse", "HEAD") + ("-dirty" if dirty else "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.parity_export")
    parser.add_argument("area", choices=sorted(AREAS))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    files = importlib.import_module(AREAS[args.area]).export()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        write_canonical(args.out, name, obj)
    write_canonical(args.out, "MANIFEST.json", {
        "area": args.area,
        # The output path is left out so two exports into different folders compare equal.
        "command": f"python -m scripts.parity_export {args.area} --out <dir>",
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_commit": _engine_commit(),
        "files": sorted(files),
        "python_version": platform.python_version(),
    })
    print(f"wrote {len(files) + 1} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
