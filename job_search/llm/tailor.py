"""Deterministic CV tailoring: the model selects base bullets; we render them.

The model returns a structured selection of existing base bullets (llm/cv_edits)
and a deterministic renderer (latex/tailor_render) rebuilds the CV from the
trusted base. Every emitted line is verbatim base content, so the result is
always a compilable subset of the base and cannot fabricate claims — replacing
the previous full-LaTeX generation, its fabrication guard, and its repair retry
(audit Finding 19).
"""
from ..latex.tailor_render import render_tailored
from ..models import coerce_job
from .cv_edits import select_cv_bullets


class CVValidationError(ValueError):
    """Raised when even the full-base render violates factual constraints."""

    def __init__(self, violations):
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


def tailor_resume(
    client, tailoring_instructions: str, base_tex: str, job: dict, profile,
    prompts=None,
) -> str:
    """Select relevant base bullets via the model, then render deterministically.

    `tailoring_instructions` is retained for call-site compatibility; the
    structured selection prompt no longer needs the large static instruction
    block. A subset render should always pass the content guard; the full-base
    fallback is defensive. `profile` is required — it is the only source of
    CV_DISPLAY_NAME / CV_FILENAME_PREFIX / employer_order once configured, and
    a caller that forgets to thread it should fail loudly here rather than
    silently render with the wrong identity (see bug #1).
    """
    job = coerce_job(job)
    employer_order = profile.employer_order
    selection = select_cv_bullets(
        client, base_tex, job, profile, prompts=prompts
    )
    tex = render_tailored(base_tex, selection, employer_order)
    validate = profile.validate_tex
    if not validate(tex):
        return tex

    tex = render_tailored(base_tex, {}, employer_order)
    remaining = validate(tex)
    if remaining:
        raise CVValidationError(remaining)
    return tex
