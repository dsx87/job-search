"""Local Jev runs and read-only regeneration of historical comparison reports.

The corpus and raw results contain posting text; the report shows URLs. All stay ignored.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from job_search.config import PipelineConfig, load_criteria
from job_search.models import Job
from job_search.pipeline.stages import clean_job_description, fetch_job_text_from_url
from job_search.text import section_aware_excerpt

from job_search.jev import (
    JEV_URL, JEV_MODEL, CHOICES, SIGNAL_QUESTIONS, SIGNAL_LABELS,
    SIGNAL_SUMMARIES, REJECTION_SIGNALS, jev_payload, parse_jev_response, call_jev,
)
REPEATS = 5
# Older ignored reports used the comparison-only platform taxonomy. Keep their
# report-only rendering available without importing the retired evaluator.
SIGNAL_LABELS = {**SIGNAL_LABELS, "platform": "Platform"}
SIGNAL_SUMMARIES = {**SIGNAL_SUMMARIES, "platform": {
    "native_apple": "native iOS/macOS focus", "cross_platform": "cross-platform main stack",
    "other": "other main stack", "unknown": "main platform unclear",
}}
SIGNAL_SUMMARIES["location"] = {**SIGNAL_SUMMARIES["location"],
                                "israel": "local arrangement allowed"}
REJECTION_SIGNALS = {**REJECTION_SIGNALS, "platform": {"cross_platform", "other"}}


def freeze_jobs(state_path, corpus_path, fetcher=fetch_job_text_from_url, excluded_urls=()):
    saved = json.loads(Path(state_path).read_text())["jobs"]
    by_source = defaultdict(list)
    excluded_urls = set(excluded_urls)
    for item in saved.values():
        if item.get("url", "").startswith(("http://", "https://")) and item["url"] not in excluded_urls:
            by_source[item.get("source") or "unknown"].append(item)
    # Interleave sources, preferring newer entries within each source.
    for group in by_source.values():
        group.sort(key=lambda item: item.get("date_posted") or "", reverse=True)
    pending = list(sorted(by_source))
    frozen = []
    while pending and len(frozen) < 4:
        for source in pending[:]:
            if not by_source[source]:
                pending.remove(source)
                continue
            item = by_source[source].pop(0)
            description = section_aware_excerpt(clean_job_description(fetcher(item["url"])), 5000)
            if len(description) >= 200:
                frozen.append({key: item.get(key) for key in (
                    "title", "company", "location", "url", "source", "is_remote", "region", "date_posted"
                )} | {"description": description})
                print(f"Frozen job {len(frozen)} from {source} ({len(description)} chars)", flush=True)
            if len(frozen) == 4:
                break
    if frozen:
        private_write(corpus_path, {"frozen_at": datetime.now(timezone.utc).isoformat(), "jobs": frozen})
    return frozen


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)


def private_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)


def summarize(raw):
    summary = []
    for index, item in enumerate(raw, 1):
        current = Counter(run["verdict"] for run in item["current"])
        jev = Counter(run["verdict"] for run in item["jev"])
        conflicting = sum(
            (run["verdict"] == "fit" and any_red) or
            (run["verdict"] == "nonfit" and not named_red)
            for run in item["jev"] if run.get("signals")
            for any_red in [any(signal["choice"] in REJECTION_SIGNALS[name]
                                for name, signal in run["signals"].items())]
            for named_red in [any(name != "unknown_red_flag" and
                                  signal["choice"] in REJECTION_SIGNALS[name]
                                  for name, signal in run["signals"].items())]
        )
        summary.append({
            "label": f"Job {index}", "source": item["source"],
            "current": current, "jev": jev,
            "current_stable": len(current) == 1 and sum(current.values()) == REPEATS,
            "jev_stable": len(jev) == 1 and sum(jev.values()) == REPEATS,
            "disagreement": current != jev,
            "jev_models": sorted({run["model"] for run in item["jev"]}),
            "findings_conflict": conflicting,
            "signals": {name: Counter(
                run["signals"][name]["choice"] for run in item["jev"]
                if name in run.get("signals", {})
            ) for name in (set(SIGNAL_QUESTIONS) | {name for run in item["jev"]
                                                   for name in run.get("signals", {})})},
        })
    return summary


def report(summary, baseline_note, jobs, jev_requests):
    if len(summary) != len(jobs) or len(jobs) != len(jev_requests):
        raise ValueError("Report jobs and results do not align")
    question = jev_requests[0]["questions"]
    if any(request["questions"] != question for request in jev_requests):
        raise ValueError("Jev questions differ across jobs")
    remote_count = sum(bool(job.get("is_remote")) for job in jobs)
    invalid_count = sum(bool(job.get("invalid_reason")) for job in jobs)
    historical = any(row["current"] for row in summary)
    heading = "# Job fit comparison" if historical else "# Jev job decisions"
    coverage = f"Coverage: {remote_count} flagged remote and {len(jobs) - remote_count} not flagged remote. Invalid samples: {invalid_count}; their verdicts are retained for audit but excluded from conclusions."
    intro = ("Five independent calls per evaluator per frozen posting. Each URL was fetched once; both evaluators received the same frozen description. " if historical else
             "Five independent Jev calls per frozen posting. Each URL was fetched once. ")
    lines = [heading, "", intro + coverage, "", baseline_note, "", "🔴 Red = Jev found a rejection rule. 🟢 Green = Jev found a rule satisfied. 🟡 Unclear = missing or mixed evidence. These are separate Jev classifications, not quotations from the posting or proof that the overall verdict is correct.", ""]
    if historical:
        lines += ["| Job | Source | URL | Current counts | Current stable | Jev counts | Jev stable | Disagree | Jev actual model |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    else:
        lines += ["| Job | Source | URL | Jev counts | Jev stable | Jev actual model |", "| --- | --- | --- | --- | --- | --- |"]
    for row, job in zip(summary, jobs):
        counts = lambda c: ", ".join(f"{key} {c[key]}" for key in CHOICES if c[key])
        label = row["label"] + (" (invalid sample)" if job.get("invalid_reason") else "")
        if historical:
            lines.append(f"| {label} | {row['source']} | <{job['url']}> | {counts(row['current'])} | {'yes' if row['current_stable'] else 'no'} | {counts(row['jev'])} | {'yes' if row['jev_stable'] else 'no'} | {'yes' if row['disagreement'] else 'no'} | {', '.join(row['jev_models'])} |")
        else:
            lines.append(f"| {label} | {row['source']} | <{job['url']}> | {counts(row['jev'])} | {'yes' if row['jev_stable'] else 'no'} | {', '.join(row['jev_models'])} |")
    if historical:
        lines += ["", "Disagree means the five-verdict distributions differ after mapping the current evaluator's `uncertain` to Jev's `review`. Agreement or repeat stability alone does not establish correctness."]
    else:
        lines += ["", "Repeat stability alone does not establish correctness."]
    if any(any(row["signals"].values()) for row in summary):
        lines += ["", "## Red and green flags", "", "A 5/5 count means the same flag appeared on every Jev run. Mixed or unknown checks appear in yellow."]
        for row, job in zip(summary, jobs):
            if not any(row["signals"].values()):
                continue
            lines += ["", f"### {row['label']} — <{job['url']}>", ""]
            if job.get("invalid_reason"):
                lines.append(f"- ⚠️ **Invalid sample:** {job['invalid_reason']} Verdicts above are retained for audit only.")
                continue
            grouped = {"red": [], "green": [], "unclear": []}
            for name, counts in row["signals"].items():
                if not counts:
                    continue
                def finding_text(choice, count):
                    detail = ""
                    if name == "location" and choice == "restricted" and job.get("location"):
                        detail = f" (posting location: {job['location']})"
                    elif name == "platform" and choice == "cross_platform":
                        framework = re.search(r"\b(React Native|Flutter|Xamarin|Ionic|Kotlin Multiplatform)\b",
                                              job.get("description") or "", re.IGNORECASE)
                        if framework:
                            detail = f" (mentions {framework.group(1)})"
                    return f"{SIGNAL_SUMMARIES[name][choice]} {count}/{REPEATS}{detail}"

                findings = ", ".join(finding_text(choice, count) for choice, count in counts.most_common())
                if len(counts) > 1 or "unknown" in counts:
                    group = "unclear"
                elif next(iter(counts)) in REJECTION_SIGNALS[name]:
                    group = "red"
                else:
                    group = "green"
                grouped[group].append(f"{SIGNAL_LABELS[name]}: {findings}")
            lines.append("- 🔴 **Red flags:** " + ("; ".join(grouped["red"]) if grouped["red"] else "None identified by these checks."))
            lines.append("- 🟢 **Green flags:** " + ("; ".join(grouped["green"]) if grouped["green"] else "None identified by these checks."))
            if grouped["unclear"]:
                lines.append("- 🟡 **Unclear or mixed:** " + "; ".join(grouped["unclear"]))
            if row["findings_conflict"]:
                lines.append(f"- ⚠️ **Verdict needs review:** The overall Jev verdict has no matching red flag, or conflicts with one, on {row['findings_conflict']}/{REPEATS} runs.")
    lines += ["", "## Jev questions", "", "The same questions were sent on each call. Job descriptions and the complete state remain in ignored local files.", "", "```json", json.dumps(question, indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="Local job_state.json path")
    parser.add_argument("--settings", required=True, help="Private TOML settings path")
    parser.add_argument("--local-dir", default=".jev-comparison/jev-only")
    parser.add_argument("--exclude-corpus", action="append", default=[], help="Skip URLs in an earlier local corpus")
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="Regenerate the local report from saved corpus and raw results")
    parser.add_argument("--jev-url", default=os.environ.get("JEV_API_URL", JEV_URL))
    parser.add_argument("--jev-model", default=os.environ.get("JEV_MODEL", JEV_MODEL))
    args = parser.parse_args(argv)
    local = Path(args.local_dir)
    corpus_path = local / "corpus.json"
    if corpus_path.exists():
        jobs = json.loads(corpus_path.read_text())["jobs"]
    else:
        excluded = {job["url"] for path in args.exclude_corpus for job in json.loads(Path(path).read_text())["jobs"]}
        jobs = freeze_jobs(args.state, corpus_path, excluded_urls=excluded)
    if not jobs:
        print("No saved URL yielded at least 200 characters. Provide a real job description to continue.", file=sys.stderr)
        return 2
    if args.freeze_only:
        return 0
    note = "Historical baseline results are retained only when reading an older saved report."
    os.environ["JOB_SEARCH_SETTINGS_FILE"] = str(Path(args.settings).resolve())
    cfg = PipelineConfig.from_env()
    settings_dir = Path(args.settings).resolve().parent
    criteria = load_criteria(str(settings_dir / cfg.criteria_file))
    requests = [jev_payload(
        Job(**{key: item.get(key) for key in Job._FIELD_NAMES if key in item}),
        criteria, cfg.candidate, cfg.policy, cfg.search, model=args.jev_model,
    ) for item in jobs]
    if args.report_only:
        raw = json.loads((local / "raw.json").read_text())
        saved_requests = [entry.get("jev_request") or request for entry, request in zip(raw, requests)]
        private_write_text(local / "report.md", report(summarize(raw), note, jobs, saved_requests))
        print(f"Report: {local / 'report.md'}")
        return 0
    if not os.environ.get("JEV_API_KEY"):
        print("JEV_API_KEY is required for live Jev calls.", file=sys.stderr)
        return 2
    raw = []
    for index, item in enumerate(jobs, 1):
        job = Job(**{key: item.get(key) for key in Job._FIELD_NAMES if key in item})
        payload = requests[index - 1]
        entry = {"source": job.source, "jev_request": payload, "current": [], "jev": []}
        for repeat in range(REPEATS):
            result = call_jev(payload, os.environ["JEV_API_KEY"], url=args.jev_url)
            entry["jev"].append(result)
            private_write(local / "raw.json", raw + [entry])
            print(f"Job {index} Jev {repeat + 1}/{REPEATS}", flush=True)
        raw.append(entry)
    private_write_text(local / "report.md", report(summarize(raw), note, jobs, requests))
    print(f"Report: {local / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
