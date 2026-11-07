"""Judge corpus for gate G3: 20 postings from the engine, five Jev calls each.

Every path is an argument and the Jev key comes only from ``JEV_API_KEY``, so no
private file or setting lives in this public repository. Output (all private):
``postings.json`` (frozen before the first Jev call) and ``jev/<corpus-id>.json``.
"""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from job_search.identity import normalize_url
from job_search.jev import evaluate_job, jev_payload
from job_search.models import coerce_job
from job_search.pipeline.stages import ensure_job_description

from scripts.compare_jev import private_write_text

CORPUS_SIZE = 20
REPEATS = 5
POSTING_FIELDS = ("source", "url", "title", "company", "location", "is_remote", "description")


class CorpusError(Exception):
    """A stop the caller reports and exits non-zero on."""


def corpus_id(url) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def stride_indices(n, size=CORPUS_SIZE):
    return [round(i * n / size) for i in range(size)]


def walk(n, usable, size=CORPUS_SIZE):
    """Engine-order indices of the chosen postings (Decision 34).

    ``usable(index)`` is called at most once per index. A planned index that is
    rejected, or already tried, falls to the next untried index after it; then any
    remaining untried index is scanned in order until ``size`` succeed."""
    tried, chosen = set(), []

    def attempt(start):
        """The index tried, or None when no untried index is left at or after ``start``."""
        index = next((j for j in range(start, n) if j not in tried), None)
        if index is not None:
            tried.add(index)
            if usable(index):
                chosen.append(index)
        return index

    for planned in stride_indices(n, size):
        index = attempt(planned)
        if index is not None and index not in chosen:
            attempt(index + 1)
    for index in range(n):
        if len(chosen) >= size:
            break
        if index not in tried:
            attempt(index)
    return sorted(chosen)


def build_postings(candidates, enrich=ensure_job_description, size=CORPUS_SIZE):
    """The corpus entries; fewer than ``size`` means the Jev part is blocked."""
    jobs = [coerce_job(candidate) for candidate in candidates]
    indices = walk(len(jobs), lambda index: enrich(jobs[index]), size)
    postings = []
    for index in indices:
        job = jobs[index]
        postings.append({"corpus_id": corpus_id(job.url), **{key: job[key] for key in POSTING_FIELDS}})
    check_ids(postings)
    return postings


def check_ids(postings):
    ids = [posting["corpus_id"] for posting in postings]
    if any(not normalize_url(posting["url"]) for posting in postings):
        raise CorpusError("a corpus posting has no URL alias")
    if len(set(ids)) != len(ids):
        raise CorpusError("two corpus postings share a corpus ID")


def source_counts(postings):
    return dict(sorted(Counter(posting["source"] for posting in postings).items()))


def write_json(path, obj):
    """Private (0600) and atomic, so an interrupted append never loses saved answers."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    private_write_text(tmp, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def load_postings(path, size=CORPUS_SIZE):
    postings = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = {"corpus_id", *POSTING_FIELDS}
    if not isinstance(postings, list) or any(
        not isinstance(posting, dict) or set(posting) != keys for posting in postings
    ):
        raise CorpusError(f"{path}: not a corpus postings array")
    if len(postings) != size:
        raise CorpusError(f"blocked: {len(postings)} usable postings in {path}")
    check_ids(postings)
    return postings


def jev_request(posting, criteria, cfg, model):
    """The payload evaluate_job sends, as it reads back from a saved file."""
    payload = jev_payload(coerce_job(posting), criteria, cfg.candidate, cfg.policy, cfg.search, model=model)
    return json.loads(json.dumps(payload))


def saved_answers(path, request):
    """The answers already saved for this request; an error if they were for another one."""
    path = Path(path)
    if not path.exists():
        return []
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("jev_request") != request:
        raise CorpusError(f"{path}: saved jev_request differs from the new one; not calling Jev")
    return list(saved["answers"])


def run_jev(postings, out, criteria, cfg, api_key, *, jev_url, jev_model, opener, repeats=REPEATS):
    """Up to ``repeats`` evaluate_job answers per posting, each saved as soon as it arrives.

    Every saved request is checked before the first call, so a mismatch stops the run
    without spending one."""
    jev_dir = Path(out) / "jev"
    plan = []
    for posting in postings:
        request = jev_request(posting, criteria, cfg, jev_model)
        path = jev_dir / f"{posting['corpus_id']}.json"
        plan.append((posting, request, path, saved_answers(path, request)))
    calls = 0
    for posting, request, path, answers in plan:
        while len(answers) < repeats:
            answers.append(evaluate_job(
                api_key, criteria, coerce_job(posting), candidate=cfg.candidate,
                policy=cfg.policy, search=cfg.search, opener=opener, url=jev_url,
            ))
            write_json(path, {"jev_request": request, "answers": answers})
            calls += 1
            print(f"{posting['corpus_id']} Jev {len(answers)}/{repeats}", flush=True)
    return calls
