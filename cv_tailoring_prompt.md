# CV tailoring instructions

This public file is retained for compatibility; its instruction text is not
supplied to the model. Active tailoring instructions come from the built-in
bullet-selection prompt, or `cv_bullet_selection.txt` in the configured
`prompt_dir` (which also requires a nonempty `prompt_revision`). Candidate
identity, work history, page limits, and factual claim restrictions belong
in the selected candidate configuration
and base template; this file contains no candidate-specific information.

## STEP 1 — Read the posting

Read the full job posting. Identify the role, required skills, work location,
and factual restrictions that affect the candidate's eligibility.

## STEP 2 — Assess fit

State only evidence supported by the configured candidate profile and trusted
base CV. Do not invent work history, qualifications, metrics, employers, or
industry experience.

## STEP 3 — Tailor the CV

The application selects existing bullets from the configured trusted LaTeX
template. Keep relevant existing bullets in the configured employer order.
Do not rewrite or add facts. Respect configured claim restrictions and the
configured page limit.

## BASE LaTeX TEMPLATE

The base template is configured separately. The public example is
`avery_example_base.tex` for Avery Example, with Example Labs followed by
Sample Systems.
