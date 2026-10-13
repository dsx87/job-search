"""Jev's wire contract, conservative routing, and public digest labels."""
import io
import json
import urllib.error

import pytest

from job_search.config import CandidateConfig, PipelineConfig, PolicyConfig, SearchConfig
from job_search.digest.fixtures import sample_context, sample_fit
from job_search.digest.telegraph import CONTENT_LIMIT_BYTES, content_size, render_digest_nodes
from job_search.jev import SIGNAL_QUESTIONS, call_jev, evaluate_job, jev_payload
from job_search.models import Job
from job_search.pipeline.stages import ensure_job_description, fetch_job_text_from_url
from job_search.runtime import Runtime, preflight
from job_search.config import ConfigurationError


def reply(verdict="fit", **signals):
    answers = {}
    for name, choices in {"fit": {"fit": "", "nonfit": "", "review": ""},
                          **SIGNAL_QUESTIONS}.items():
        choice = verdict if name == "fit" else signals.get(name, "no" if name == "unknown_red_flag" else "unknown")
        answers[name] = {"type": "choice", "choice": choice,
                         "probabilities": {key: int(key == choice) for key in choices}}
    return {"model": "jev-1.13.0", "answers": answers}


def evaluate(response):
    def opener(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret"
        assert json.loads(request.data)["state"]["candidate_criteria"] == "approved criteria"
        return io.BytesIO(json.dumps(response).encode())
    return evaluate_job("secret", "approved criteria", Job(title="Engineer", description="Posting details " * 20),
                        candidate=CandidateConfig(), policy=PolicyConfig(), search=SearchConfig(),
                        opener=opener)


def test_payload_has_all_approved_questions_and_settings():
    payload = jev_payload(Job(title="Engineer", description="Details"), "criteria",
                          CandidateConfig(residency_countries=("IL",)),
                          PolicyConfig(excluded_industries=("crypto",)),
                          SearchConfig(role_include_terms=("engineer",)))
    assert list(payload["questions"]) == ["fit", *SIGNAL_QUESTIONS]
    assert payload["state"]["candidate_settings"]["residency_countries"] == ("IL",)
    assert payload["state"]["policy_settings"]["excluded_industries"] == ("crypto",)
    assert payload["state"]["search_settings"]["role_include_terms"] == ("engineer",)


def test_retries_rate_limit_then_succeeds(monkeypatch):
    payload = jev_payload(Job(), "criteria", CandidateConfig(), PolicyConfig(), SearchConfig())
    attempts = []
    def opener(_request, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise urllib.error.HTTPError("https://api.defapi.org", 429, "slow", {}, None)
        return io.BytesIO(json.dumps(reply()).encode())
    sleeps = []
    monkeypatch.setattr("job_search.jev.time.sleep", sleeps.append)
    assert call_jev(payload, "secret", opener=opener)["verdict"] == "fit"
    assert attempts == [90, 90]
    assert sleeps == [1]


@pytest.mark.parametrize("result, expected", [
    (reply("fit", technology_stack="pass"), "fit"),
    (reply("review", technology_stack="reject"), "review"),
    (reply("nonfit", industry="reject"), "nonfit"),
    (reply("fit", industry="reject"), "review"),
    (reply("nonfit", unknown_red_flag="yes"), "review"),
    (reply("nonfit"), "review"),
])
def test_conservative_routing(result, expected):
    evaluation = evaluate(result)
    assert evaluation["verdict"] == expected
    assert evaluation["fit"] is (expected == "fit")
    assert evaluation["signals"]["location"] == result["answers"]["location"]["choice"]


def test_malformed_signal_retries_next_run_by_raising():
    response = reply()
    del response["answers"]["industry"]
    with pytest.raises(ValueError, match="industry"):
        evaluate(response)


def test_telegraph_uses_short_categorical_labels_with_no_posting_quotes():
    evaluation = evaluate(reply("review", industry="reject", technology_stack="pass",
                                unknown_red_flag="unknown"))
    entry = sample_fit()
    entry.evaluation = evaluation
    nodes = render_digest_nodes(sample_context(fits=[entry], review=[]))
    rendered = json.dumps(nodes, ensure_ascii=False)
    assert "🔴 Industry: industry excluded" in rendered
    assert "🟢 Technology stack: main stack allowed" in rendered
    assert "🟡 Unknown red flag: another requirement may be unmet" in rendered
    assert "Posting details" not in rendered
    assert content_size(nodes) < CONTENT_LIMIT_BYTES


def test_generic_careers_page_is_deferred():
    job = Job(title="iOS Engineer", url="", description="Browse all jobs and explore open positions. " * 10)
    assert not ensure_job_description(job)


def test_careers_footer_does_not_defer_a_real_posting():
    job = Job(title="Senior Backend Engineer", url="", description=(
        "Build backend services and own production systems. " * 12) + " View all jobs")
    assert ensure_job_description(job)


def test_redirect_to_careers_index_is_deferred(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.close()
        def geturl(self):
            return "https://example.com/jobs"
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response(b"Browse jobs"))
    assert fetch_job_text_from_url("https://example.com/jobs/123") == ""


def test_jev_key_is_environment_only_and_preflight_required(tmp_path, monkeypatch):
    criteria = tmp_path / "criteria.md"
    criteria.write_text("Fictional candidate criteria")
    monkeypatch.setenv("JEV_API_KEY", "secret")
    config = PipelineConfig.from_env()
    assert config.jev_api_key == "secret"
    runtime = Runtime(object(), object(), object(), object(), object(),
                      needs_base_tex=False, needs_telegram=False)
    with pytest.raises(ConfigurationError, match="JEV_API_KEY"):
        preflight(PipelineConfig(criteria_file=str(criteria)), runtime, command="daily")
    with pytest.raises(ConfigurationError, match="JEV_API_KEY"):
        preflight(PipelineConfig(criteria_file=str(criteria)), runtime, command="check")


def test_telegraph_stays_under_limit_with_hundred_fits():
    rows = [{"color": "green", "label": name, "description": "criterion allowed by policy"}
            for name in ("Language", "Location and work", "Employment", "Seniority",
                         "Technology stack", "Industry")]
    entries = []
    for index in range(100):
        entry = sample_fit(title="Role {}".format(index), url="https://example.com/{}".format(index))
        entry.evaluation = {"reason": "Jev classified this posting as a fit.", "findings": rows}
        entries.append(entry)
    assert content_size(render_digest_nodes(sample_context(fits=entries, review=[]))) < CONTENT_LIMIT_BYTES
