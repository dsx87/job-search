"""Offline contract checks for the gate G3 judge corpus (scripts/judge_corpus).

Synthetic postings, a fake Jev opener and a fake key only: no network, no private file.
"""
import hashlib
import json
import random
import stat
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO

import pytest

from job_search.jev import APPROVED_CHECKS, CHOICES, JEV_MODEL
from job_search.pipeline import stages
from scripts import judge_corpus
from scripts.judge_corpus import __main__ as cli

KEY = "test-key-jev"
JEV_URL = "https://jev.invalid/api/v1/decisions"
EXAMPLE_TOML = Path(__file__).resolve().parent.parent / "job_search.example.toml"
CRITERIA = "Synthetic criteria: remote Swift roles only.\n"
SOURCES = ["remotive", "remoteok", "weworkremotely"]
BASE_TEXT = " ".join(["Build and ship Swift applications for a remote-first team."] * 8)
POSTING_KEYS = ["corpus_id", "source", "url", "title", "company", "location", "is_remote", "description"]
EVEN_40 = list(range(0, 40, 2))


class Interrupted(Exception):
    """Stands in for a crash or Ctrl-C between two Jev calls."""


def candidate(i, **override):
    job = {
        "source": SOURCES[i % 3],
        "url": f"https://Jobs.Example.test/Role/{i}/",
        "title": f"Role {i}",
        "company": f"Company {i}",
        "location": "Remote, Europe",
        "is_remote": True,
        "description": f"{BASE_TEXT} Posting {i}.",
    }
    job.update(override)
    return job


def short(i, **override):
    """A candidate whose text is too short and whose URL cannot be fetched."""
    return candidate(i, description="Too short.", url=f"no-scheme.example.test/{i}", **override)


def alias_id(alias):
    return hashlib.sha256(alias.encode("utf-8")).hexdigest()[:16]


def expected_posting(i):
    """Posting entry for candidate(i), the URL alias written out by hand."""
    job = candidate(i)
    return {
        "corpus_id": alias_id(f"https://jobs.example.test/role/{i}"),
        "source": job["source"], "url": job["url"], "title": job["title"],
        "company": job["company"], "location": job["location"],
        "is_remote": True, "description": job["description"],
    }


def choice_answer(labels, chosen):
    return {"type": "choice", "choice": chosen,
            "probabilities": {label: 1.0 if label == chosen else 0.0 for label in labels}}


class FakeJev:
    """Opener double. Never prints or stores the request headers (they carry the key)."""

    def __init__(self, fail_after=None, on_call=None):
        self.fail_after = fail_after
        self.on_call = on_call
        self.attempts = 0
        self.bodies = []          # parsed request body of each completed call
        self.titles = []          # job title of each completed call
        self.languages = {}       # title -> language choices in the order they were returned

    def __call__(self, request, timeout):
        self.attempts += 1
        if request.full_url != JEV_URL:
            raise AssertionError(f"Jev called at {request.full_url}")
        if request.get_header("Authorization") != "Bearer " + KEY:
            raise AssertionError("Jev request carried the wrong authorization header")
        if self.on_call:
            self.on_call()
        if self.fail_after is not None and len(self.titles) >= self.fail_after:
            raise Interrupted()
        body = json.loads(request.data)
        title = body["state"]["job"]["title"]
        language = ("pass", "reject", "unknown")[len(self.titles) % 3]
        answers = {"fit": choice_answer(CHOICES, "review")}
        for name, check in APPROVED_CHECKS.items():
            labels = tuple(check["choices"])
            answers[name] = choice_answer(labels, language if name == "language" else labels[0])
        self.bodies.append(body)
        self.titles.append(title)
        self.languages.setdefault(title, []).append(language)
        return BytesIO(json.dumps({"model": JEV_MODEL, "answers": answers}).encode())


class Workspace:
    def __init__(self, tmp_path):
        self.out = tmp_path / "out"
        self.criteria = tmp_path / "criteria.md"
        self.criteria.write_text(CRITERIA, encoding="utf-8")

    def argv(self, model=JEV_MODEL):
        return ["--toml", str(EXAMPLE_TOML), "--criteria-md", str(self.criteria),
                "--jev-url", JEV_URL, "--jev-model", model, "--out", str(self.out)]

    @property
    def postings_path(self):
        return self.out / "postings.json"

    @property
    def jev_dir(self):
        return self.out / "jev"

    def freeze(self, n=20):
        postings = judge_corpus.build_postings([candidate(i) for i in range(n)], enrich=lambda job: True)
        judge_corpus.write_json(self.postings_path, postings)
        return postings

    def read_postings(self):
        return json.loads(self.postings_path.read_text(encoding="utf-8"))

    def saved(self):
        """corpus_id -> parsed jev file."""
        if not self.jev_dir.exists():
            return {}
        return {path.stem: json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.jev_dir.glob("*.json"))}

    def snapshot(self):
        return {path.name: path.read_bytes() for path in sorted(self.jev_dir.glob("*"))} \
            if self.jev_dir.exists() else {}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    fetched, pages = [], {}

    def fetch_text(url):
        fetched.append(url)
        return pages.get(url, "")

    def no_fetch(*args, **kwargs):
        raise AssertionError("a source fetch was attempted")

    monkeypatch.setattr(stages, "fetch_job_text_from_url", fetch_text)
    monkeypatch.setattr(cli, "_fetch", no_fetch)
    return SimpleNamespace(fetched=fetched, pages=pages)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", KEY)
    return Workspace(tmp_path)


def serve(monkeypatch, jobs, usable=True):
    """Make the CLI's fetch return these candidates; the list records the source names asked for."""
    asked = []

    def fetch(names, cfg):
        asked.append(list(names))
        return SimpleNamespace(has_usable_source=usable, jobs=list(jobs))

    monkeypatch.setattr(cli, "_fetch", fetch)
    return asked


def walk_with(n, rejected=()):
    tried = []

    def usable(index):
        tried.append(index)
        return index not in rejected

    return judge_corpus.walk(n, usable), tried


# --- corpus ID ---------------------------------------------------------------------------

def test_corpus_id_is_first_16_hex_of_sha256_of_the_normalized_url_alias():
    # sha256("https://example.com/jobs/1") = 3fbd7ba759842c42fa111ccb..., worked out by hand
    assert judge_corpus.corpus_id("HTTPS://Example.com/Jobs/1/") == "3fbd7ba759842c42"


@pytest.mark.parametrize("spelling", [
    "https://example.com/jobs/1",
    "https://example.com/jobs/1/",
    "https://example.com/jobs/1//",
    "HTTPS://EXAMPLE.COM/JOBS/1",
    "  https://example.com/jobs/1/  ",
])
def test_corpus_id_ignores_case_whitespace_and_trailing_slashes(spelling):
    assert judge_corpus.corpus_id(spelling) == "3fbd7ba759842c42"


def test_corpus_id_differs_for_different_urls():
    assert judge_corpus.corpus_id("https://example.com/jobs/1") != judge_corpus.corpus_id("https://example.com/jobs/2")


def test_corpus_id_is_16_lowercase_hex_characters():
    value = judge_corpus.corpus_id("https://jobs.example.test/role/a-1")
    assert value == "50bde561cd6ddd1d"
    assert len(value) == 16 and set(value) <= set("0123456789abcdef")


# --- stride walk -------------------------------------------------------------------------

def test_stride_indices_are_round_i_n_over_20():
    assert judge_corpus.stride_indices(40) == EVEN_40
    assert judge_corpus.stride_indices(20) == list(range(20))
    assert judge_corpus.stride_indices(100) == list(range(0, 100, 5))


def test_stride_indices_round_halves_as_python_round_does():
    # i*30/20 = 1.5*i; Python's round() takes the half cases to the even integer.
    assert judge_corpus.stride_indices(30) == [
        0, 2, 3, 4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 21, 22, 24, 26, 27, 28,
    ]


def test_walk_takes_exactly_the_planned_indices_when_all_are_usable():
    chosen, tried = walk_with(40)
    assert chosen == EVEN_40
    assert tried == EVEN_40


def test_walk_takes_every_posting_when_there_are_fewer_than_20():
    chosen, tried = walk_with(7)
    assert chosen == list(range(7))
    assert sorted(tried) == list(range(7))


def test_walk_of_an_empty_list_chooses_nothing():
    assert walk_with(0) == ([], [])


def test_walk_takes_the_next_unused_index_after_a_rejected_planned_one():
    chosen, tried = walk_with(40, rejected={2})
    assert chosen == [0, 3] + EVEN_40[2:]
    assert tried.count(2) == 1 and tried.count(3) == 1


def test_walk_tries_only_one_index_after_a_rejected_planned_one_before_the_scan():
    # 2 and 3 are rejected. The scan then takes the first unused index in engine order, 1,
    # and not 5 or the last index.
    chosen, _ = walk_with(40, rejected={2, 3})
    assert chosen == [0, 1] + EVEN_40[2:]


def test_walk_scans_the_remaining_indices_in_engine_order():
    # planned 2 and 4 and their next indices 3 and 5 are rejected: 18 chosen, the scan adds 1 then 7.
    chosen, _ = walk_with(40, rejected={2, 3, 4, 5})
    assert chosen == [0, 1, 6, 7] + list(range(8, 39, 2))


def test_walk_falls_back_to_the_last_index_when_the_last_planned_one_is_rejected():
    chosen, _ = walk_with(40, rejected={38})
    assert chosen == EVEN_40[:-1] + [39]


def test_walk_scan_fills_in_from_the_start_when_the_last_two_are_rejected():
    chosen, _ = walk_with(40, rejected={38, 39})
    assert chosen == [0, 1] + EVEN_40[1:-1]


def test_walk_with_fewer_than_20_usable_returns_all_of_them_and_tries_every_index_once():
    usable_set = set(range(12))
    tried = []

    def usable(index):
        tried.append(index)
        return index in usable_set

    chosen = judge_corpus.walk(40, usable)
    assert chosen == list(range(12))
    assert sorted(tried) == list(range(40))


def test_walk_with_one_rejected_in_a_list_of_exactly_20_returns_19_without_a_double_try():
    chosen, tried = walk_with(20, rejected={3})
    assert chosen == [i for i in range(20) if i != 3]
    assert sorted(tried) == list(range(20))


def test_walk_stops_enriching_once_20_are_chosen():
    chosen, tried = walk_with(60, rejected={3})
    assert chosen == [0, 4] + list(range(6, 60, 3))[:18]
    assert tried == [0, 3, 4] + list(range(6, 60, 3))[:18]


def test_walk_properties_hold_on_random_lists_and_rejections():
    seed = 20261107
    rng = random.Random(seed)
    for case in range(300):
        n = rng.choice([0, 1, 5, 19, 20, 21, 25, 39, 40, 41, 60, 97, 130])
        rate = rng.choice([0, 0.1, 0.3, 0.6, 0.9, 1])
        rejected = {i for i in range(n) if rng.random() < rate}
        chosen, tried = walk_with(n, rejected)
        context = f"seed={seed} case={case} n={n} rejected={sorted(rejected)}"
        usable_all = [i for i in range(n) if i not in rejected]
        assert len(tried) == len(set(tried)), f"index enriched twice: {context}"
        assert all(0 <= i < n for i in tried), context
        assert chosen == sorted(set(chosen)), f"chosen not sorted and distinct: {context}"
        assert set(chosen) <= set(usable_all), f"chose a rejected index: {context}"
        assert len(chosen) == min(20, len(usable_all)), f"wrong count {len(chosen)}: {context}"
        planned_usable = {i for i in judge_corpus.stride_indices(n) if i < n and i not in rejected}
        assert planned_usable <= set(chosen), f"a usable planned index was skipped: {context}"


# --- build_postings ----------------------------------------------------------------------

def test_postings_have_the_documented_keys_in_order_and_follow_engine_order():
    # titles run backwards, so any sort by title or by corpus ID would show
    jobs = [candidate(i, title=f"Z{40 - i:03d}") for i in range(40)]
    postings = judge_corpus.build_postings(jobs, enrich=lambda job: True)
    assert [list(posting) for posting in postings] == [POSTING_KEYS] * 20
    assert [posting["title"] for posting in postings] == [f"Z{40 - i:03d}" for i in EVEN_40]
    assert [posting["corpus_id"] for posting in postings] == [
        alias_id(f"https://jobs.example.test/role/{i}") for i in EVEN_40
    ]


def test_postings_hold_the_job_values_and_leave_out_other_fields():
    postings = judge_corpus.build_postings([candidate(i) for i in range(40)], enrich=lambda job: True)
    assert postings == [expected_posting(i) for i in EVEN_40]
    assert all(type(posting["is_remote"]) is bool for posting in postings)


def test_postings_keep_unicode_text_through_the_written_file(tmp_path):
    jobs = [candidate(i) for i in range(20)]
    jobs[0] = candidate(0, title="Ingénieur Swift 日本語", company="Zoë & Søn")
    postings = judge_corpus.build_postings(jobs, enrich=lambda job: True)
    path = tmp_path / "postings.json"
    judge_corpus.write_json(path, postings)
    assert json.loads(path.read_text(encoding="utf-8"))[0]["title"] == "Ingénieur Swift 日本語"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["company"] == "Zoë & Søn"


def test_postings_carry_the_enriched_description(offline):
    short_url = "https://jobs.example.test/role/5"
    offline.pages[short_url] = "<p>Fetched <b>full</b> text.</p> " + BASE_TEXT
    jobs = [candidate(i) for i in range(20)]
    jobs[5] = candidate(5, url=short_url, description="Short.")
    jobs[6] = candidate(6, description="<div><p>Hello <b>world</b></p></div> " + BASE_TEXT)
    postings = judge_corpus.build_postings(jobs)
    assert offline.fetched == [short_url]
    assert postings[5]["description"] == "Fetched full text. " + BASE_TEXT
    assert "<" not in postings[6]["description"] and postings[6]["description"].startswith("Hello world")


def test_postings_never_enrich_an_index_twice_with_the_real_enrichment(offline):
    jobs = [candidate(i) for i in range(40)]
    for i in (2, 3):
        jobs[i] = candidate(i, description="Short.")
    postings = judge_corpus.build_postings(jobs)
    assert sorted(offline.fetched) == sorted({jobs[2]["url"], jobs[3]["url"]})
    assert len(offline.fetched) == 2
    assert [p["title"] for p in postings] == ["Role 0", "Role 1"] + [f"Role {i}" for i in EVEN_40[2:]]


def test_build_postings_with_fewer_than_20_usable_returns_them_for_the_caller_to_block_on():
    jobs = [candidate(i) if i < 19 else short(i) for i in range(40)]
    postings = judge_corpus.build_postings(jobs)
    assert [p["title"] for p in postings] == [f"Role {i}" for i in range(19)]


def test_two_postings_with_the_same_url_alias_raise_instead_of_sharing_an_id():
    jobs = [candidate(i) for i in range(20)]
    jobs[4] = candidate(4, url="HTTPS://jobs.example.test/ROLE/3")
    with pytest.raises(judge_corpus.CorpusError, match="share a corpus ID"):
        judge_corpus.build_postings(jobs)


def test_a_chosen_posting_without_a_url_raises():
    jobs = [candidate(i) for i in range(20)]
    jobs[7] = candidate(7, url="")
    with pytest.raises(judge_corpus.CorpusError, match="no URL alias"):
        judge_corpus.build_postings(jobs)


def test_source_counts_are_sorted_by_source_name():
    postings = [{"source": s} for s in ["remotive", "remoteok", "remotive", "weworkremotely", "remotive"]]
    counts = judge_corpus.source_counts(postings)
    assert counts == {"remoteok": 1, "remotive": 3, "weworkremotely": 1}
    assert list(counts) == ["remoteok", "remotive", "weworkremotely"]


# --- main: building the corpus -----------------------------------------------------------

def test_main_fetches_with_the_generic_sources_and_freezes_the_walked_postings(ws, monkeypatch, capsys):
    from job_search.sources.fetch import select_sources
    asked = serve(monkeypatch, [candidate(i) for i in range(40)])
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 0

    assert asked == [list(select_sources(generic=True))]
    assert ws.read_postings() == [expected_posting(i) for i in EVEN_40]
    assert "usable postings per source: {'remoteok': 6, 'remotive': 7, 'weworkremotely': 7}" in capsys.readouterr().out


def test_main_writes_postings_before_the_first_jev_call(ws, monkeypatch):
    serve(monkeypatch, [candidate(i) for i in range(40)])
    seen = []
    fake = FakeJev(on_call=lambda: seen.append(ws.postings_path.exists() and ws.read_postings()))

    cli.main(ws.argv(), opener=fake)

    assert seen[0] == [expected_posting(i) for i in EVEN_40]


def test_main_blocks_with_19_usable_postings_making_no_call_and_writing_nothing(ws, monkeypatch, capsys):
    serve(monkeypatch, [candidate(i) if i < 19 else short(i) for i in range(40)])
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    err = capsys.readouterr().err
    assert "blocked" in err and "19" in err
    assert fake.attempts == 0
    assert not ws.postings_path.exists() and not ws.jev_dir.exists()


def test_main_goes_on_with_exactly_20_usable_postings(ws, monkeypatch):
    serve(monkeypatch, [candidate(i) if i < 20 else short(i) for i in range(40)])
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 0

    assert [p["title"] for p in ws.read_postings()] == [f"Role {i}" for i in range(20)]
    assert len(fake.titles) == 100


def test_main_makes_no_call_and_writes_nothing_when_no_source_completed(ws, monkeypatch, capsys):
    serve(monkeypatch, [candidate(i) for i in range(40)], usable=False)
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    assert "No usable job source" in capsys.readouterr().err
    assert fake.attempts == 0
    assert not ws.out.exists()


def test_main_stops_before_any_call_when_two_walked_postings_share_an_alias(ws, monkeypatch, capsys):
    jobs = [candidate(i) for i in range(20)]
    jobs[4] = candidate(4, url="https://jobs.example.test/role/3/")
    serve(monkeypatch, jobs)
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    assert "share a corpus ID" in capsys.readouterr().err
    assert fake.attempts == 0 and not ws.postings_path.exists()


def test_main_reuses_frozen_postings_without_fetching(ws, capsys):
    frozen = ws.freeze()
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 0   # the autouse fetch double raises if called

    assert ws.read_postings() == frozen
    assert sorted(fake.languages) == sorted(p["title"] for p in frozen)
    assert "reusing 20 frozen postings" in capsys.readouterr().out


def damaged_postings(kind):
    postings = [expected_posting(i) for i in range(20)]
    if kind == "19 postings":
        return postings[:19]
    if kind == "21 postings":
        return postings + [expected_posting(20)]
    if kind == "empty array":
        return []
    if kind == "object":
        return {"postings": postings}
    if kind == "missing key":
        del postings[3]["description"]
    if kind == "extra key":
        postings[3]["region"] = "eu"
    if kind == "duplicate id":
        postings[5]["corpus_id"] = postings[4]["corpus_id"]
    if kind == "empty url":
        postings[5]["url"] = ""
    if kind == "not an object":
        postings[5] = "text"
    return postings


@pytest.mark.parametrize("kind", [
    "19 postings", "21 postings", "empty array", "object", "missing key", "extra key",
    "duplicate id", "empty url", "not an object",
])
def test_main_rejects_a_damaged_frozen_postings_file_before_any_call(ws, kind, capsys):
    ws.out.mkdir()
    ws.postings_path.write_text(json.dumps(damaged_postings(kind)), encoding="utf-8")
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    assert capsys.readouterr().err.strip() != ""
    assert fake.attempts == 0 and not ws.jev_dir.exists()


def test_main_reports_the_count_for_a_frozen_file_with_too_few_postings(ws, capsys):
    ws.out.mkdir()
    ws.postings_path.write_text(json.dumps(damaged_postings("19 postings")), encoding="utf-8")

    assert cli.main(ws.argv(), opener=FakeJev()) == 1

    assert "blocked: 19 usable postings" in capsys.readouterr().err


# --- main: guards before any call --------------------------------------------------------

def test_main_exits_without_a_call_when_the_jev_model_differs_from_the_engine(ws, capsys):
    ws.freeze()
    fake = FakeJev()

    assert cli.main(ws.argv(model="typesafe/jev-9.99"), opener=fake) == 2

    err = capsys.readouterr().err
    assert "typesafe/jev-9.99" in err and JEV_MODEL in err
    assert fake.attempts == 0 and not ws.jev_dir.exists()


def test_main_checks_the_jev_model_before_touching_the_output_directory(ws):
    fake = FakeJev()

    assert cli.main(ws.argv(model="other-model"), opener=fake) == 2

    assert fake.attempts == 0 and not ws.out.exists()


@pytest.mark.parametrize("value", [None, ""])
def test_main_exits_without_a_call_when_the_jev_key_is_missing(ws, monkeypatch, value, capsys):
    ws.freeze()
    if value is None:
        monkeypatch.delenv("JEV_API_KEY")
    else:
        monkeypatch.setenv("JEV_API_KEY", value)
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 2

    assert "JEV_API_KEY" in capsys.readouterr().err
    assert fake.attempts == 0


# --- main: saved answers and resume ------------------------------------------------------

def test_each_jev_file_holds_the_request_and_five_engine_answers_in_call_order(ws):
    postings = ws.freeze()
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 0

    saved = ws.saved()
    assert sorted(saved) == sorted(p["corpus_id"] for p in postings)
    for posting in postings:
        entry = saved[posting["corpus_id"]]
        assert set(entry) == {"jev_request", "answers"}
        bodies = [b for b in fake.bodies if b["state"]["job"]["title"] == posting["title"]]
        assert len(bodies) == 5 and all(b == bodies[0] for b in bodies)
        assert entry["jev_request"] == bodies[0]
        assert entry["jev_request"]["model"] == JEV_MODEL
        assert entry["jev_request"]["state"]["candidate_criteria"] == CRITERIA
        assert len(entry["answers"]) == 5
        for answer in entry["answers"]:
            assert set(answer) == {"fit", "verdict", "reason", "signals", "findings", "jev_model"}
            assert answer["verdict"] == "review" and answer["jev_model"] == JEV_MODEL
            assert set(answer["signals"]) == set(APPROVED_CHECKS)
        assert [a["signals"]["language"] for a in entry["answers"]] == fake.languages[posting["title"]]


@pytest.mark.parametrize("done", [0, 1, 5, 7, 99])
def test_interrupted_run_resumes_so_all_runs_make_exactly_100_calls(ws, done):
    postings = ws.freeze()
    first = FakeJev(fail_after=done)
    with pytest.raises(Interrupted):
        cli.main(ws.argv(), opener=first)
    assert len(first.titles) == done
    after_first = ws.saved()
    assert sum(len(entry["answers"]) for entry in after_first.values()) == done

    second = FakeJev()
    assert cli.main(ws.argv(), opener=second) == 0

    assert len(second.titles) == 100 - done
    for posting in postings:
        title, entry = posting["title"], ws.saved()[posting["corpus_id"]]
        assert (first.titles + second.titles).count(title) == 5
        assert len(entry["answers"]) == 5
        kept = after_first.get(posting["corpus_id"], {"answers": []})["answers"]
        assert entry["answers"][:len(kept)] == kept
        assert [a["signals"]["language"] for a in entry["answers"]] == \
            first.languages.get(title, []) + second.languages.get(title, [])

    before_third = ws.snapshot()
    third = FakeJev()
    assert cli.main(ws.argv(), opener=third) == 0
    assert third.attempts == 0
    assert ws.snapshot() == before_third


def test_a_run_interrupted_mid_posting_has_saved_each_answer_as_it_arrived(ws):
    postings = ws.freeze()
    with pytest.raises(Interrupted):
        cli.main(ws.argv(), opener=FakeJev(fail_after=7))

    saved = ws.saved()
    assert len(saved[postings[0]["corpus_id"]]["answers"]) == 5
    assert len(saved[postings[1]["corpus_id"]]["answers"]) == 2
    assert postings[2]["corpus_id"] not in saved
    assert not list(ws.jev_dir.glob("*.tmp"))


def test_changed_criteria_stop_a_resume_with_an_error_before_any_call(ws, capsys):
    ws.freeze()
    with pytest.raises(Interrupted):
        cli.main(ws.argv(), opener=FakeJev(fail_after=7))
    before = ws.snapshot()
    ws.criteria.write_text(CRITERIA + "One more rule.\n", encoding="utf-8")
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    assert "differs" in capsys.readouterr().err
    assert fake.attempts == 0
    assert ws.snapshot() == before


def test_a_mismatch_in_the_last_posting_file_stops_the_run_before_the_first_call(ws, capsys):
    postings = ws.freeze()
    last = ws.jev_dir / f"{postings[-1]['corpus_id']}.json"
    judge_corpus.write_json(last, {"jev_request": {"model": "something else"}, "answers": []})
    before = ws.snapshot()
    fake = FakeJev()

    assert cli.main(ws.argv(), opener=fake) == 1

    err = capsys.readouterr().err
    assert last.name in err and "differs" in err
    assert fake.attempts == 0
    assert ws.snapshot() == before


def test_a_saved_file_with_the_same_request_is_continued_not_replaced(ws):
    postings = ws.freeze()
    with pytest.raises(Interrupted):
        cli.main(ws.argv(), opener=FakeJev(fail_after=3))
    first_file = ws.jev_dir / f"{postings[0]['corpus_id']}.json"
    kept = json.loads(first_file.read_text(encoding="utf-8"))["answers"]
    assert len(kept) == 3

    cli.main(ws.argv(), opener=FakeJev())

    assert json.loads(first_file.read_text(encoding="utf-8"))["answers"][:3] == kept


# --- privacy -----------------------------------------------------------------------------

def test_output_files_are_private_and_the_key_is_never_written_or_printed(ws, monkeypatch, capsys):
    serve(monkeypatch, [candidate(i) for i in range(40)])
    assert cli.main(ws.argv(), opener=FakeJev()) == 0

    files = [ws.postings_path, *sorted(ws.jev_dir.glob("*"))]
    assert len(files) == 21
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
        assert KEY not in path.read_text(encoding="utf-8"), path.name
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err


def test_a_key_with_a_trailing_carriage_return_is_stripped_and_inner_control_characters_are_refused(monkeypatch, capsys):
    """Review fix: a CRLF .env leaves "\r" on the key; http.client would raise with the key in
    the message. A trailing CR/LF is stripped; a control character inside stops before any call."""
    from scripts.judge_corpus import __main__ as cli

    monkeypatch.setenv("JEV_API_KEY", "test-key\x01-jev")
    calls = []
    rc = cli.main(
        ["--toml", "job_search.example.toml", "--criteria-md", "criteria.md", "--jev-url", "https://jev.invalid",
         "--jev-model", cli.JEV_MODEL, "--out", "/nonexistent/should-not-be-created"],
        opener=lambda *a, **k: calls.append(a),
    )
    out = capsys.readouterr()
    assert rc == 2 and not calls
    assert "test-key" not in out.err and "test-key" not in out.out

