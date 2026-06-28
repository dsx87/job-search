"""LLM CV tailoring with a deterministic content guard + one corrective retry."""
import sys

from ..latex.compile import _strip_latex_fences
from ..profile import EXPECTED_JOB_ORDER, validate_tailored_cv


def tailor_resume(client, tailoring_instructions: str, base_tex: str, job: dict) -> str:
    """Returns tailored LaTeX source (code fences stripped).

    Generates at temperature 0.0 for factual stability, then runs a
    deterministic guard (validate_tailored_cv). On violations it regenerates
    once with a corrective instruction; if the second pass still fails, it logs
    the remaining violations and returns the best attempt.
    """
    job_text = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"URL: {job.get('url', '')}\n\n"
        f"{job.get('description', '')[:7000]}"
    )
    prompt = f"""You are a professional resume writer. Tailor Igor Pivnyk's CV for the job posting below.

{tailoring_instructions}

## Produce the LaTeX file

Write the complete, compilable LaTeX source. Start from the base template and apply your changes. \
Output the entire .tex file — do not truncate. Output raw LaTeX only — no markdown fences, \
no explanation before or after.

## Base LaTeX Template

{base_tex}

## Job Posting

{job_text}
"""
    tex = _strip_latex_fences(client.generate(prompt, temperature=0.0))
    violations = validate_tailored_cv(tex)
    if not violations:
        return tex

    print(f"    CV guard caught violations: {'; '.join(violations)} — regenerating once.", flush=True)
    corrective = prompt + f"""

## CORRECTION REQUIRED

Your previous attempt violated these hard constraints:
{chr(10).join(f"- {v}" for v in violations)}

Regenerate the complete LaTeX file fixing exactly these issues. The four jobs must appear in this \
fixed order: {' → '.join(EXPECTED_JOB_ORDER)}. Do not claim any industry/domain or skill Igor does \
not have. Output raw LaTeX only.
"""
    tex2 = _strip_latex_fences(client.generate(corrective, temperature=0.0))
    remaining = validate_tailored_cv(tex2)
    if remaining:
        print(
            f"    CV guard: violations persist after retry: {'; '.join(remaining)} — "
            f"delivering best attempt, REVIEW BEFORE SENDING.",
            file=sys.stderr,
        )
    return tex2
