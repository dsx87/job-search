"""TDD for the one-line job summariser used by the digest dashboard."""
from job_search.llm.summarize import summarize_job
from job_search.models import Job


def test_returns_collapsed_single_line_summary(fake_llm):
    client = fake_llm(["  Senior iOS role building a\n  consumer Swift app.  "])
    job = Job(title="iOS Engineer", company="Acme", description="x" * 400)

    summary = summarize_job(client, job)

    assert summary == "Senior iOS role building a consumer Swift app."


def test_prompt_includes_posting_details(fake_llm):
    client = fake_llm(["ok"])
    job = Job(title="iOS Engineer", company="Acme", description="Build the flagship app.")

    summarize_job(client, job)

    prompt = client.prompts[0]
    assert "iOS Engineer" in prompt
    assert "Acme" in prompt
    assert "Build the flagship app." in prompt


def test_llm_failure_returns_empty_string_and_never_raises(fake_llm):
    client = fake_llm([RuntimeError("llm down")])
    job = Job(title="iOS Engineer", company="Acme", description="x" * 400)

    assert summarize_job(client, job) == ""


def test_overlong_summary_is_bounded(fake_llm):
    client = fake_llm(["word " * 200])
    job = Job(title="iOS Engineer", company="Acme", description="x" * 400)

    summary = summarize_job(client, job)

    assert len(summary) <= 300
    assert summary.endswith("…")
