"""`python -m job_search` / `python -m job_search.scraper_cli` — the scraper CLI.

Argument parsing, the interactive menu, and dispatch into sources.fetch.
"""
import argparse
import json
import sys

from .config import PipelineConfig
from .filters.rules import DEFAULT_RELOCATION_REGIONS
from .sources import ALL_SOURCES, SOURCE_DESCRIPTIONS
from .sources.fetch import (
    parse_regions,
    parse_sources,
    print_sources,
    regions_for_display,
    run_scraper,
    select_sources,
    source_names_for_display,
)


def _search_options(settings):
    if not getattr(settings, "settings_file", ""):
        return {}
    return {"search": settings.search, "candidate": settings.candidate,
            "budget_seconds": settings.scrape_budget_seconds}


def _source_names(raw, settings):
    if (raw or "").strip().lower() == "all" and getattr(settings, "settings_file", ""):
        return select_sources(generic=True)
    return parse_sources(raw)


def choose_sources_interactively(current):
    print("")
    names = list(ALL_SOURCES.keys())
    print("Available sources:")
    for index, name in enumerate(names, start=1):
        print("  {:2}. {:18} {}".format(index, name, SOURCE_DESCRIPTIONS.get(name, "")))
    print("")
    print("Enter comma-separated source names, numbers, or 'all'.")
    print("Current: {}".format(source_names_for_display(current)))
    raw = input("Sources> ").strip()
    if not raw:
        return current
    if raw.lower() == "all":
        return None

    selected = []
    invalid = []
    for part in [item.strip().lower() for item in raw.split(",") if item.strip()]:
        if part.isdigit():
            index = int(part) - 1
            if 0 <= index < len(names):
                selected.append(names[index])
            else:
                invalid.append(part)
        elif part in ALL_SOURCES:
            selected.append(part)
        else:
            invalid.append(part)
    if invalid:
        print("Ignored unknown selections: {}".format(", ".join(invalid)))
    return selected or current


def choose_regions_interactively(current):
    print("")
    print("Available regions: eu, ca, au, us")
    print("Current: {}".format(regions_for_display(current)))
    raw = input("Regions> ").strip()
    if not raw:
        return current
    return parse_regions(raw)


def choose_max_age_interactively(current):
    print("")
    raw = input("Max age in days (0 keeps all, current {}): ".format(current)).strip()
    if not raw:
        return current
    try:
        value = int(raw)
    except ValueError:
        print("Invalid number; keeping current value.")
        return current
    return max(0, value)


def interactive_menu(initial_args=None, settings=None):
    source_names = None
    regions = set(DEFAULT_RELOCATION_REGIONS)
    max_age = 30
    as_json = False
    verbose = False

    if initial_args is not None:
        source_names = _source_names(initial_args.sources, settings)
        regions = parse_regions(initial_args.relocation_region)
        max_age = initial_args.max_age
        as_json = initial_args.as_json
        verbose = initial_args.verbose

    while True:
        print("")
        print("Job Scraper")
        print("1. Run scraper")
        print("2. Choose sources        [{}]".format(source_names_for_display(source_names)))
        print("3. Choose regions        [{}]".format(regions_for_display(regions)))
        print("4. Set max job age       [{} days]".format(max_age))
        print("5. Toggle JSON output    [{}]".format("on" if as_json else "off"))
        print("6. Toggle verbose output [{}]".format("on" if verbose else "off"))
        print("7. List sources")
        print("8. Quit")
        choice = input("> ").strip().lower()

        if choice in ("", "1", "run", "r"):
            return run_scraper(
                source_names=source_names,
                relocation_regions=regions,
                max_age=max_age,
                as_json=as_json,
                verbose=verbose,
                **_search_options(settings),
            )
        if choice in ("2", "sources", "s"):
            source_names = choose_sources_interactively(source_names)
            if source_names is None and getattr(settings, "settings_file", ""):
                source_names = select_sources(generic=True)
        elif choice in ("3", "regions", "region"):
            regions = choose_regions_interactively(regions)
        elif choice in ("4", "age", "max-age"):
            max_age = choose_max_age_interactively(max_age)
        elif choice in ("5", "json", "j"):
            as_json = not as_json
        elif choice in ("6", "verbose", "v"):
            verbose = not verbose
        elif choice in ("7", "list", "l"):
            print_sources()
        elif choice in ("8", "quit", "q", "exit"):
            return 0
        else:
            print("Unknown option.")


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Scrape matching remote jobs globally and relocation/visa jobs "
            "in selected regions. With no arguments, opens an interactive menu."
        )
    )
    parser.add_argument("--describe-config", action="store_true",
                        help="Print versioned configuration metadata without loading hooks.")
    parser.add_argument("--validate-config", metavar="PATH",
                        help="Validate TOML data without runtime checks or side effects.")
    parser.add_argument(
        "--sources",
        default=None,
        help="Comma-separated sources or 'all' (the default-on set). Use "
        "--list-sources to see names; sources marked (default: off) run only "
        "when named explicitly.",
    )
    parser.add_argument(
        "--relocation-region",
        "--region",
        dest="relocation_region",
        default=None,
        help="Comma-separated relocation regions: eu, ca, au, us. Remote jobs are global.",
    )
    parser.add_argument(
        "--max-age",
        default=None,
        type=int,
        help="Max job age in days. Use 0 to keep all dates. Default: 30.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output JSON instead of plain text.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug information from each source.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print available source names and exit.",
    )
    parser.add_argument(
        "--menu",
        action="store_true",
        help="Open the interactive menu even when other arguments are present.",
    )
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.describe_config:
        from .settings import option_metadata
        print(json.dumps(option_metadata(), indent=2, sort_keys=True))
        return 0
    if args.validate_config:
        from .settings import validate_settings_path
        try:
            validate_settings_path(args.validate_config)
        except (ValueError, OSError) as exc:
            print("Error: {}".format(exc), file=sys.stderr)
            return 2
        print("Valid configuration: {}".format(args.validate_config))
        return 0

    if args.list_sources:
        print_sources()
        return 0

    try:
        settings = PipelineConfig.from_env()
    except (ValueError, OSError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 2
    configured = bool(getattr(settings, "settings_file", ""))
    if args.max_age is None:
        args.max_age = settings.search.max_age_days if configured else 30
    if args.relocation_region is None:
        args.relocation_region = ",".join(settings.search.relocation_regions) if configured else "eu,ca,us"
    if args.sources is None and configured:
        args.sources = ",".join(select_sources(settings.sources_enable, settings.sources_disable, generic=True))
    if args.menu or (not argv and sys.stdin.isatty()):
        return interactive_menu(args, settings)

    return run_scraper(
        source_names=_source_names(args.sources, settings),
        relocation_regions=parse_regions(args.relocation_region),
        max_age=args.max_age,
        as_json=args.as_json,
        verbose=args.verbose,
        **_search_options(settings),
    )


if __name__ == "__main__":
    raise SystemExit(main())
