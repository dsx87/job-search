"""LLM CV tailoring with a deterministic content guard + one corrective retry."""

from ..latex.compile import _strip_latex_fences
from ..models import coerce_job
from ..profile import EXPECTED_JOB_ORDER, validate_tailored_cv
from ..text import section_aware_excerpt


class CVValidationError(ValueError):
    """Raised when a tailored CV still violates factual constraints."""

    def __init__(self, violations):
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


def tailor_resume(client, tailoring_instructions: str, base_tex: str, job: dict) -> str:
    """Returns tailored LaTeX source (code fences stripped).

    Requests temperature 0.0 for providers that support deterministic sampling;
    Gemini 3 clients omit that parameter to retain Google's recommended default.
    A deterministic guard (validate_tailored_cv) checks the result. On violations
    it regenerates once with a corrective instruction; if the second pass still
    fails, it raises CVValidationError so invalid content cannot reach delivery.
    """
    job = coerce_job(job)
    job_text = (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"URL: {job.get('url', '')}\n\n"
        f"{section_aware_excerpt(job.get('description', ''), 7000)}"
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
        raise CVValidationError(remaining)
    return tex2
