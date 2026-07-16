"""Structured CV bullet extraction + deterministic rendering (audit order 7).

extract_job_bullets pulls the four experience \\jobheader+itemize blocks out of
the base CV; render_tailored rebuilds each itemize from a {company: [indices]}
selection while leaving everything else (header, summary, skills, education,
languages) untouched. Both are pure/deterministic — no LLM, no network.
"""
import re

# --- modules under test (repoint on migration) ---
from job_search.config import load_base_tex
from job_search.latex.tailor_render import extract_job_bullets, render_tailored
from job_search.profile import EXPECTED_JOB_ORDER, validate_tailored_cv


def _base():
    return load_base_tex()


def _by_company(tex):
    return {job["company"]: job for job in extract_job_bullets(tex)}


def _texts(job):
    """Bullet bodies with outer whitespace normalized (round-trip artifacts)."""
    return [bullet.strip() for bullet in job["bullets"]]


def _itemize_bodies(tex):
    """Every \\begin{itemize}...\\end{itemize} body in document order."""
    return re.findall(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", tex, re.DOTALL)


# --- extract_job_bullets ------------------------------------------------

def test_extract_job_bullets_maps_four_jobs_in_order():
    jobs = extract_job_bullets(_base())

    assert len(jobs) == 4
    assert [job["company"] for job in jobs] == EXPECTED_JOB_ORDER
    assert [len(job["bullets"]) for job in jobs] == [6, 3, 3, 4]


def test_extract_job_bullets_keeps_text_verbatim_without_item():
    jobs = extract_job_bullets(_base())
    check_point = jobs[0]

    assert check_point["company"] == "Check Point"
    # a distinctive phrase survives verbatim inside a Check Point bullet
    assert any("Trusted Network Detection" in bullet for bullet in check_point["bullets"])
    # the leading control sequence is stripped from every bullet body
    for job in jobs:
        for bullet in job["bullets"]:
            assert "\\item" not in bullet


# --- render_tailored: selection ----------------------------------------

def test_render_tailored_selects_subset_for_one_company():
    base = _base()
    original = extract_job_bullets(base)[0]["bullets"]  # Check Point

    out = render_tailored(base, {"Check Point": [0, 2]})

    rendered = _by_company(out)
    # Check Point keeps exactly the two selected bullets, in the given order
    assert _texts(rendered["Check Point"]) == [original[0].strip(), original[2].strip()]
    # the other three companies are untouched
    assert len(rendered["Applitools"]["bullets"]) == 3
    assert len(rendered["Shutterfly"]["bullets"]) == 3
    assert len(rendered["CNOGA"]["bullets"]) == 4


def test_render_tailored_preserves_selection_order():
    base = _base()

    out = render_tailored(base, {"Check Point": [2, 0]})

    # index-2 bullet ("architectural refactor") is emitted before index-0
    # bullet ("unified cross-platform logging"); both phrases are unique.
    assert out.index("architectural refactor") < out.index("unified cross-platform logging")


# --- render_tailored: invalid / empty selections never break the itemize

def test_render_tailored_ignores_invalid_indices_and_never_empties_itemize():
    base = _base()
    cp = extract_job_bullets(base)[0]["bullets"]

    # every index invalid (out of range, negative, non-int) -> keep all bullets
    all_invalid = render_tailored(base, {"Check Point": [99, -1, "x", None]})
    assert len(_by_company(all_invalid)["Check Point"]["bullets"]) == 6

    # a mix drops only the invalid ones, preserving order of the valid ones
    mixed = render_tailored(base, {"Check Point": [0, 99, 2, "nope"]})
    assert _texts(_by_company(mixed)["Check Point"]) == [cp[0].strip(), cp[2].strip()]

    # an empty list and an omitted company both keep ALL bullets
    empty_and_omitted = render_tailored(base, {"Applitools": []})
    assert len(_by_company(empty_and_omitted)["Applitools"]["bullets"]) == 3  # [] -> keep all
    assert len(_by_company(empty_and_omitted)["CNOGA"]["bullets"]) == 4       # omitted -> keep all

    # no itemize block is ever emitted empty
    for variant in (all_invalid, mixed, empty_and_omitted):
        assert "\\begin{itemize}\n\\end{itemize}" not in variant
        for body in _itemize_bodies(variant):
            assert "\\item" in body


# --- render_tailored: everything outside the itemize blocks is intact ---

def test_render_tailored_leaves_content_outside_itemize_intact():
    base = _base()

    out = render_tailored(base, {})

    for token in ("((PHONE))", "Senior iOS and macOS engineer", "Languages"):
        assert token in out
    for company in EXPECTED_JOB_ORDER:
        assert company in out
    # jobheaders untouched + only base content emitted -> validator is clean
    assert validate_tailored_cv(render_tailored(base, {"Check Point": [0]})) == []


def test_render_tailored_emits_no_forbidden_terms():
    base = _base()

    out = render_tailored(
        base,
        {"Check Point": [0, 2], "Applitools": [1], "CNOGA": [0, 3]},
    )

    assert validate_tailored_cv(out) == []


def test_render_tailored_dedups_repeated_indices():
    # A duplicated index must NOT duplicate the bullet in the rendered CV.
    base = load_base_tex()
    out = render_tailored(base, {"Check Point": [0, 0, 1]})
    cp = next(j for j in extract_job_bullets(out) if j["company"] == "Check Point")
    assert len(cp["bullets"]) == 2  # deduped: [0, 1], not [0, 0, 1]
    assert cp["bullets"][0] != cp["bullets"][1]
    assert validate_tailored_cv(out) == []
