"""LLM job evaluation against the fit criteria."""
import json


def evaluate_job(client, criteria: str, job: dict) -> dict:
    """Returns {"fit": bool, "reason": str, "timezone_note": str|None}."""
    prompt = f"""You are evaluating a job posting for Igor Pivnyk, an iOS/macOS developer based in Israel (UTC+3).

## Fit Criteria

{criteria}

## Job Posting

Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Remote: {job.get("is_remote", "")}
Source: {job.get("source", "")}
URL: {job.get("url", "")}

Description:
{job.get("description", "")[:5000]}

## Your Task

Decide whether this job fits Igor's criteria. Return a JSON object with exactly these fields:
- "fit": true or false
- "reason": one or two sentences explaining the decision
- "timezone_note": a warning string if the role requires strict US business hours only, otherwise null
"""
    raw = client.generate(prompt, temperature=0.0, json_mode=True)
    result = json.loads(raw)
    if isinstance(result.get("fit"), str):
        result["fit"] = result["fit"].lower() == "true"
    return result
