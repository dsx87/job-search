"""Deterministic CV tailoring: the model selects base bullets; we render them.

The model returns a structured selection of existing base bullets (llm/cv_edits)
and a deterministic renderer (latex/tailor_render) rebuilds the CV from the
trusted base. Every emitted line is verbatim base content, so the result is
always a compilable subset of the base and cannot fabricate claims — replacing
the previous full-LaTeX generation, its fabrication guard, and its repair retry
(audit Finding 19).
"""
from ..latex.tailor_render import OPTIONAL_SELECTION_KEY, render_tailored
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
    return tailor_resume_candidates(
        client, tailoring_instructions, base_tex, job, profile, prompts=prompts
    )[0]


def tailor_resume_candidates(
    client, tailoring_instructions: str, base_tex: str, job: dict, profile,
    prompts=None,
) -> list:
    """Like ``tailor_resume`` but return every guard-passing render, best first.

    With one model call this yields the selection with its chosen optional
    sections, then the same selection without them (the page-limit fallback);
    only if neither passes the content guard, the full base without optional
    sections.
    """
    job = coerce_job(job)
    employer_order = profile.employer_order
    selection = select_cv_bullets(
        client, base_tex, job, profile, prompts=prompts
    ) or {}
    validate = profile.validate_tex
    without_optional = {
        key: value for key, value in selection.items() if key != OPTIONAL_SELECTION_KEY
    }
    rendered = []
    candidates = []
    for chosen in (selection, without_optional):
        tex = render_tailored(base_tex, chosen, employer_order)
        if tex in rendered:
            continue
        rendered.append(tex)
        if not validate(tex):
            candidates.append(tex)
    if candidates:
        return candidates

    tex = render_tailored(base_tex, {}, employer_order)
    remaining = validate(tex)
    if remaining:
        raise CVValidationError(remaining)
    return [tex]
