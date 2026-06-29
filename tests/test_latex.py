"""Characterization tests for LaTeX string ops and the CV validator."""
# --- modules under test (repoint on migration) ---
from job_search.profile import validate_tailored_cv, EXPECTED_JOB_ORDER
from job_search.latex.compile import (
    _strip_latex_fences,
    pdf_pages_from_log,
    _extract_latex_errors,
)
from job_search.latex.onepage import _apply_density_overrides, ONE_PAGE_SHRINK_LADDER


def _cv(order=("Check Point", "Applitools", "Shutterfly", "CNOGA"), extra=""):
    headers = "\n".join(f"\\jobheader{{{c} Ltd}}" for c in order)
    return (
        "\\documentclass[9.5pt]{article}\n"
        "\\begin{document}\n"
        f"{headers}\n"
        "\\jobheader{University of Somewhere}\n"
        f"{extra}\n"
        "\\end{document}"
    )


def test_strip_latex_fences_removes_fence_and_extracts_document():
    fenced = "```latex\n\\documentclass{x}\\begin{document}hi\\end{document}\n```"
    assert _strip_latex_fences(fenced) == "\\documentclass{x}\\begin{document}hi\\end{document}"


def test_strip_latex_fences_extracts_from_prose():
    prose = "Here you go:\n\\documentclass{x}\\begin{document}hi\\end{document}\nHope it helps!"
    assert _strip_latex_fences(prose) == "\\documentclass{x}\\begin{document}hi\\end{document}"


def test_strip_latex_fences_passthrough_when_no_document():
    assert _strip_latex_fences("  just text  ") == "just text"


def test_validate_tailored_cv_clean():
    assert validate_tailored_cv(_cv()) == []


def test_validate_tailored_cv_out_of_order():
    v = validate_tailored_cv(_cv(order=("Check Point", "Shutterfly", "Applitools", "CNOGA")))
    assert len(v) == 1
    assert "out of order" in v[0]


def test_validate_tailored_cv_missing_job():
    v = validate_tailored_cv(_cv(order=("Check Point", "Applitools", "Shutterfly")))
    assert any("missing job" in x for x in v)


def test_validate_tailored_cv_forbidden_term():
    v = validate_tailored_cv(_cv(extra="Deep experience in banking systems."))
    assert any("forbidden term present: 'banking'" == x for x in v)


def testpdf_pages_from_log(tmp_path):
    log = tmp_path / "cv.log"
    log.write_text("blah\nOutput written on cv.pdf (2 pages, 34567 bytes).\nmore", encoding="utf-8")
    assert pdf_pages_from_log(str(log)) == 2

    log.write_text("Output written on cv.pdf (1 page, 100 bytes).", encoding="utf-8")
    assert pdf_pages_from_log(str(log)) == 1

    log.write_text("no page line here", encoding="utf-8")
    assert pdf_pages_from_log(str(log)) is None

    assert pdf_pages_from_log(str(tmp_path / "missing.log")) is None


def test_extract_latex_errors():
    log = "intro line\n! Undefined control sequence.\nl.5 \\bogus\nmore context\n"
    out = _extract_latex_errors(log)
    assert "! Undefined control sequence." in out


def test_apply_density_overrides_lowers_font_and_inserts_block():
    src = "\\documentclass[10pt]{article}\n\\begin{document}\nbody\n\\end{document}"
    step = ONE_PAGE_SHRINK_LADDER[3]  # font 9
    out = _apply_density_overrides(src, step)
    assert "10pt" not in out
    assert "9pt" in out
    assert "one-page guard" in out
    # override block is inserted immediately before \begin{document}
    assert out.index("one-page guard") < out.index("\\begin{document}")
    assert "\\setstretch{0.86}" in out


def test_expected_job_order_constant():
    assert EXPECTED_JOB_ORDER == ["Check Point", "Applitools", "Shutterfly", "CNOGA"]
