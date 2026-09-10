"""Structured CV bullet extraction and deterministic rendering for Avery Example."""
from pathlib import Path
import re

from job_search.latex.tailor_render import extract_job_bullets, render_tailored
from job_search.profile import validate_tailored_cv


FIXTURE = Path(__file__).parent / "fixtures" / "fictional_candidate.tex"
EMPLOYERS = ("Example Labs", "Sample Systems")


def _base():
    return FIXTURE.read_text(encoding="utf-8")


def _by_company(tex):
    return {job["company"]: job for job in extract_job_bullets(tex, EMPLOYERS)}


def _texts(job):
    return [bullet.strip() for bullet in job["bullets"]]


def _validate(tex):
    return validate_tailored_cv(tex, expected_job_order=EMPLOYERS)


def _itemize_bodies(tex):
    return re.findall(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", tex, re.DOTALL)


def test_extract_job_bullets_maps_configured_fictional_jobs_in_order():
    jobs = extract_job_bullets(_base(), EMPLOYERS)

    assert [job["company"] for job in jobs] == list(EMPLOYERS)
    assert [len(job["bullets"]) for job in jobs] == [2, 2]


def test_extract_job_bullets_keeps_fictional_text_verbatim_without_item():
    jobs = extract_job_bullets(_base(), EMPLOYERS)

    assert "Implemented release automation" in jobs[0]["bullets"][0]
    assert all("\\item" not in bullet for job in jobs for bullet in job["bullets"])


def test_render_tailored_selects_subset_for_one_configured_employer():
    base = _base()
    original = extract_job_bullets(base, EMPLOYERS)[0]["bullets"]

    rendered = _by_company(render_tailored(base, {"Example Labs": [0]}, EMPLOYERS))

    assert _texts(rendered["Example Labs"]) == [original[0].strip()]
    assert len(rendered["Sample Systems"]["bullets"]) == 2


def test_render_tailored_preserves_selection_order():
    out = render_tailored(_base(), {"Example Labs": [1, 0]}, EMPLOYERS)

    assert out.index("Legacy monitoring cleanup") < out.index("Implemented release automation")


def test_render_tailored_ignores_invalid_indices_and_never_empties_itemize():
    base = _base()
    all_invalid = render_tailored(base, {"Example Labs": [99, -1, "x", None]}, EMPLOYERS)
    mixed = render_tailored(base, {"Example Labs": [0, 99, 1, "nope"]}, EMPLOYERS)
    empty_and_omitted = render_tailored(base, {"Example Labs": []}, EMPLOYERS)

    assert len(_by_company(all_invalid)["Example Labs"]["bullets"]) == 2
    assert len(_by_company(mixed)["Example Labs"]["bullets"]) == 2
    assert len(_by_company(empty_and_omitted)["Sample Systems"]["bullets"]) == 2
    for variant in (all_invalid, mixed, empty_and_omitted):
        assert "\\begin{itemize}\n\\end{itemize}" not in variant
        assert all("\\item" in body for body in _itemize_bodies(variant))


def test_render_tailored_leaves_content_outside_itemize_intact():
    out = render_tailored(_base(), {}, EMPLOYERS)

    for token in ("Avery Example", "Platform engineer", "Skills"):
        assert token in out
    assert _validate(render_tailored(out, {"Example Labs": [0]}, EMPLOYERS)) == []


def test_render_tailored_preserves_explicit_candidate_claim_guard():
    out = render_tailored(_base(), {"Example Labs": [0], "Sample Systems": [1]}, EMPLOYERS)

    assert validate_tailored_cv(
        out, expected_job_order=EMPLOYERS, forbidden_term_patterns=(r"forbidden claim",)
    ) == []


def test_render_tailored_dedups_repeated_indices():
    out = render_tailored(_base(), {"Example Labs": [0, 0, 1]}, EMPLOYERS)
    job = _by_company(out)["Example Labs"]

    assert len(job["bullets"]) == 2
    assert job["bullets"][0] != job["bullets"][1]
    assert _validate(out) == []
