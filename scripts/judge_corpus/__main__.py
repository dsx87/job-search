"""CLI: ``python -m scripts.judge_corpus --toml <p> --criteria-md <p> --jev-url <u>
--jev-model <m> --out <dir>``; the Jev key is read from ``JEV_API_KEY``."""
import argparse
import os
import sys
import urllib.request
from pathlib import Path

from job_search.config import load_criteria, require_search_configured
from job_search.jev import JEV_MODEL
from job_search.sources.fetch import select_sources

from scripts.parity_export.commands import _fetch, load_config

from . import CORPUS_SIZE, CorpusError, build_postings, load_postings, run_jev, source_counts, write_json


def main(argv=None, opener=urllib.request.urlopen) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.judge_corpus", description=__doc__)
    parser.add_argument("--toml", required=True, type=Path)
    parser.add_argument("--criteria-md", required=True, type=Path)
    parser.add_argument("--jev-url", required=True)
    parser.add_argument("--jev-model", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.jev_model != JEV_MODEL:
        print(f"--jev-model {args.jev_model} differs from the engine's JEV_MODEL {JEV_MODEL}",
              file=sys.stderr)
        return 2
    api_key = (os.environ.get("JEV_API_KEY") or "").strip()
    if not api_key:
        print("JEV_API_KEY is required.", file=sys.stderr)
        return 2
    # A key with whitespace or control characters inside (a CRLF .env) would make http.client
    # raise with the key in the message; refuse it before any call, without echoing it.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in api_key):
        print("JEV_API_KEY holds whitespace or control characters.", file=sys.stderr)
        return 2
    cfg = load_config(args.toml)
    criteria = load_criteria(str(args.criteria_md))
    postings_path = args.out / "postings.json"
    try:
        if postings_path.exists():
            postings = load_postings(postings_path)
            print(f"reusing {len(postings)} frozen postings from {postings_path}")
        else:
            require_search_configured(cfg)
            report = _fetch(select_sources(generic=True), cfg)
            if not report.has_usable_source:
                print("No usable job source completed; nothing written.", file=sys.stderr)
                return 1
            print(f"{len(report.jobs)} candidates after prefilter and dedupe")
            postings = build_postings(report.jobs)
            print(f"usable postings per source: {source_counts(postings)}")
            if len(postings) < CORPUS_SIZE:
                print(f"blocked: {len(postings)} usable postings (need {CORPUS_SIZE}); "
                      "no Jev call made, nothing written.", file=sys.stderr)
                return 1
            write_json(postings_path, postings)
            print(f"wrote {len(postings)} postings to {postings_path}")
        calls = run_jev(postings, args.out, criteria, cfg, api_key,
                        jev_url=args.jev_url, jev_model=args.jev_model, opener=opener)
    except CorpusError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"done: {calls} Jev calls made, answers under {args.out / 'jev'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
