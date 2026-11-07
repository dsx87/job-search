"""CLI: ``python -m scripts.parity_export <area> --out <dir>``, plus the commands
``import-bundle``, ``legacy-check`` and ``fetch-postings`` (see ``commands.py``)."""
import argparse
import datetime
import importlib
import platform
import sys
from pathlib import Path

from . import commands, write_canonical

AREAS = {
    name: f"scripts.parity_export.{name}"
    for name in (
        "llm", "sources", "text", "prefilter", "identity",
        "fingerprint", "enrich", "legacy", "criteria",
    )
}

COMMANDS = ("import-bundle", "legacy-check", "fetch-postings")
assert not set(COMMANDS) & set(AREAS)


def _export_area(area: str, out: Path) -> int:
    module = importlib.import_module(AREAS[area])
    files = module.export()
    out.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        write_canonical(out, name, obj)
    manifest = {
        "area": area,
        # The output path is left out so two exports into different folders compare equal.
        "command": f"python -m scripts.parity_export {area} --out <dir>",
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_commit": commands.engine_commit(),
        "files": sorted(files),
        "python_version": platform.python_version(),
    }
    notes = getattr(module, "NOTES", None)
    if notes:
        manifest["notes"] = list(notes)
    write_canonical(out, "MANIFEST.json", manifest)
    print(f"wrote {len(files) + 1} files to {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.parity_export")
    sub = parser.add_subparsers(dest="name", required=True, metavar="{area|command}")
    for area in sorted(AREAS):
        sub.add_parser(area, help=f"export the {area} goldens").add_argument(
            "--out", required=True, type=Path
        )

    bundle = sub.add_parser("import-bundle", help="write <out>/bundle.json from the legacy inputs")
    bundle.add_argument("--toml", required=True, type=Path)
    bundle.add_argument("--criteria", required=True, type=Path)
    bundle.add_argument("--seen", required=True, type=Path)
    bundle.add_argument("--job-state", required=True, type=Path)
    bundle.add_argument("--out", required=True, type=Path)

    check = sub.add_parser("legacy-check", help="legacy answer per posting with a bundle's state")
    check.add_argument("--bundle", required=True, type=Path)
    check.add_argument("--postings", required=True, type=Path)
    check.add_argument("--out", required=True, type=Path)

    fetch = sub.add_parser("fetch-postings", help="fetch live, prefilter and dedupe with the engine")
    fetch.add_argument("--toml", required=True, type=Path)
    fetch.add_argument(
        "--sources", help="comma list; omitted = the engine's generic default selection"
    )
    fetch.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.name in AREAS:
        return _export_area(args.name, args.out)

    if args.name == "import-bundle":
        return commands.import_bundle(args.toml, args.criteria, args.seen, args.job_state, args.out)
    if args.name == "legacy-check":
        return commands.legacy_check(args.bundle, args.postings, args.out)
    return commands.fetch_postings(args.toml, args.sources, args.out)


if __name__ == "__main__":
    sys.exit(main())
