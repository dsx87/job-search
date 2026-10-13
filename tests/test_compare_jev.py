"""Offline contract checks for the standalone Jev comparison."""
import io
import json

import pytest

from job_search.config import CandidateConfig, PolicyConfig, SearchConfig
from job_search.models import Job
from scripts.compare_jev import call_jev, freeze_jobs, jev_payload, parse_jev_response, report, summarize


def test_request_and_response_with_fake_http_client():
    job = Job(title="iOS Engineer", description="A remote Swift role.", is_remote=True)
    payload = jev_payload(job, "private criteria", CandidateConfig(), PolicyConfig(), SearchConfig())
    assert payload["model"] == "typesafe/jev-1.13"
    assert list(payload["questions"]) == [
        "fit", "language", "location", "employment", "seniority", "technology_stack", "industry", "unknown_red_flag",
    ]
    assert set(payload["questions"]["fit"]["criteria"]) == {"fit", "nonfit", "review"}
    reply = {"model": "jev-1.13.0", "answers": {"fit": {
        "type": "choice", "choice": "review",
        "probabilities": {"fit": .1, "nonfit": .2, "review": .7},
    }}}
    for name, question in payload["questions"].items():
        if name != "fit":
            choice = next(iter(question["criteria"]))
            reply["answers"][name] = {
                "type": "choice", "choice": choice,
                "probabilities": {key: int(key == choice) for key in question["criteria"]},
            }

    class Response(io.BytesIO):
        pass

    def opener(request, timeout):
        assert request.full_url == "https://api.defapi.org/api/v1/decisions"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer secret"
        sent = json.loads(request.data)
        assert sent == json.loads(json.dumps(payload))
        assert timeout == 90
        return Response(json.dumps(reply).encode())

    assert call_jev(payload, "secret", opener=opener) == {
        "verdict": "review", "probabilities": reply["answers"]["fit"]["probabilities"],
        "signals": {name: {"choice": answer["choice"], "probabilities": answer["probabilities"]}
                    for name, answer in reply["answers"].items() if name != "fit"},
        "model": "jev-1.13.0", "raw": reply,
    }


@pytest.mark.parametrize("response", [
    {},
    {"model": "jev-1.13.0", "answers": {}},
    {"model": "jev-1.13.0", "answers": {"fit": {"type": "choice", "choice": "maybe", "probabilities": {"fit": 1, "nonfit": 0, "review": 0}}}},
    {"model": "jev-1.13.0", "answers": {"fit": {"type": "choice", "choice": "fit", "probabilities": {"fit": .5, "nonfit": .5}}}},
    {"model": "jev-1.13.0", "answers": {"fit": {"type": "choice", "choice": "fit", "probabilities": {"fit": 1.2, "nonfit": -.2, "review": 0}}}},
])
def test_malformed_jev_response_fails(response):
    with pytest.raises(ValueError):
        parse_jev_response(response)


def test_missing_or_malformed_signal_fails():
    reply = {"model": "jev-1.13.0", "answers": {"fit": {
        "type": "choice", "choice": "fit", "probabilities": {"fit": 1, "nonfit": 0, "review": 0},
    }}}
    with pytest.raises(ValueError, match="language"):
        parse_jev_response(reply, ("fit", "language"))
    reply["answers"]["language"] = {
        "type": "choice", "choice": "pass", "probabilities": {"pass": 1, "reject": 0},
    }
    with pytest.raises(ValueError, match="language"):
        parse_jev_response(reply, ("fit", "language"))


def test_report_counts_stability_and_disagreement():
    rows = summarize([{"source": "example", "current": [
        {"verdict": "fit"}] * 5, "jev": [
        {"verdict": "fit", "model": "jev-1.13.0"}] * 4 +
        [{"verdict": "review", "model": "jev-1.13.0"}]}])
    assert rows[0]["current_stable"]
    assert not rows[0]["jev_stable"]
    assert rows[0]["disagreement"]
    rendered = report(rows, "baseline note", [{"title": "iOS Engineer", "source": "example", "url": "https://example.com/job-1", "description": "Private frozen posting text"}], [{"model": "typesafe/jev-1.13", "state": {"job": "Private frozen posting text"}, "questions": {"fit": {"type": "choice"}}}])
    assert "fit 4, review 1" in rendered
    assert "baseline note" in rendered
    assert "does not establish correctness" in rendered
    assert "<https://example.com/job-1>" in rendered
    assert "Private frozen posting text" not in rendered
    assert '"type": "choice"' in rendered


def test_report_shows_jev_findings_without_posting_text():
    raw = [{"source": "example", "current": [{"verdict": "review"}] * 5,
            "jev": [{"verdict": "nonfit", "model": "jev-1.13.0", "signals": {
                "platform": {"choice": "cross_platform", "probabilities": {}},
            }}] * 5}]
    jobs = [{"source": "example", "url": "https://example.com/job", "description": "Private posting text"}]
    requests = [{"questions": {"fit": {"type": "choice"}}}]
    rendered = report(summarize(raw), "baseline", jobs, requests)
    assert "Cross-platform" in rendered or "cross-platform" in rendered
    assert "5/5" in rendered
    assert "Private posting text" not in rendered


def test_report_flags_verdict_without_matching_rejection_signal():
    raw = [{"source": "example", "current": [{"verdict": "review"}] * 5,
            "jev": [{"verdict": "nonfit", "model": "jev-1.13.0", "signals": {
                "technology_stack": {"choice": "unknown", "probabilities": {}},
            }}] * 5}]
    rows = summarize(raw)
    assert rows[0]["findings_conflict"] == 5
    rendered = report(rows, "baseline", [{"source": "example", "url": "https://example.com/job"}],
                      [{"questions": {"fit": {"type": "choice"}}}])
    assert "Verdict needs review" in rendered
    assert "5/5 runs" in rendered


def test_report_marks_invalid_posting_and_excludes_its_flags():
    raw = [{"source": "example", "current": [{"verdict": "review"}] * 5,
            "jev": [{"verdict": "nonfit", "model": "jev-1.13.0", "signals": {
                "location": {"choice": "restricted", "probabilities": {}},
            }}] * 5}]
    jobs = [{"source": "example", "url": "https://example.com/stale",
             "invalid_reason": "Redirects to a jobs list."}]
    rendered = report(summarize(raw), "baseline", jobs,
                      [{"questions": {"fit": {"type": "choice"}}}])
    assert "Job 1 (invalid sample)" in rendered
    assert "Redirects to a jobs list." in rendered
    assert "Red flags:" not in rendered


def test_freeze_skips_urls_from_previous_set(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"jobs": {
        "old": {"url": "https://example.com/old", "source": "one", "title": "Old"},
        "new": {"url": "https://example.com/new", "source": "two", "title": "New"},
    }}))
    fetched = []

    def fetch(url):
        fetched.append(url)
        return "Remote native iOS engineer. " * 12

    jobs = freeze_jobs(state, tmp_path / "corpus.json", fetcher=fetch, excluded_urls={"https://example.com/old"})
    assert fetched == ["https://example.com/new"]
    assert [job["url"] for job in jobs] == fetched
