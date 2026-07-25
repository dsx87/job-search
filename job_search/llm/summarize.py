"""One-line, human-readable summary of a job posting for the digest dashboard.

Unlike fact extraction (llm/facts.py), this is purely presentational: a short
sentence the user reads in the results table. It must never break a run, so any
LLM failure collapses to an empty string and the dashboard simply omits it.
"""
from ..models import coerce_job
from ..text import collapse_ws, section_aware_excerpt

_MAX_SUMMARY_LEN = 300


def _bounded(text: str, limit: int = _MAX_SUMMARY_LEN) -> str:
    text = collapse_ws(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_job(client, job) -> str:
    """Return a single-sentence summary of the posting, or "" on any failure."""
    job = coerce_job(job)
    prompt = f"""Summarise this job posting in ONE short, neutral sentence (max ~30 words) that captures the role, the product/domain, and the work arrangement. Do not judge fit or add commentary; just describe the role. Return only the sentence.

Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}

Description:
{section_aware_excerpt(job.get("description", ""), 4000)}
"""
    try:
        raw = client.generate(prompt, temperature=0.0)
    except Exception:
        return ""
    return _bounded(str(raw or ""))
