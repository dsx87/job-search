"""Characterization tests for LaTeX string ops and the CV validator."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

# --- modules under test (repoint on migration) ---
from job_search.profile import validate_tailored_cv, EXPECTED_JOB_ORDER
from job_search.latex import compile as compile_mod
from job_search.latex import onepage as onepage_mod
from job_search.latex.compile import (
    CompileResult,
    _compile_latex,
    _strip_latex_fences,
    compile_with_fixes,
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


def test_validate_tailored_cv_forbidden_cpp_development():
    v = validate_tailored_cv(_cv(extra="Developed C++ shared libraries at Check Point."))
    assert any(x.startswith("forbidden term present:") and "C++" in x for x in v)


def test_validate_tailored_cv_allows_cpp_interop():
    # Swift/C++ interop is truthful and must not be flagged.
    assert validate_tailored_cv(_cv(extra="Used Swift/C++ interop; C++ Interop.")) == []


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


def _fake_xelatex(monkeypatch, returncodes, pdf_bytes=b"PDF", log_text=None):
    calls = []
    codes = iter(returncodes)

    def run(cmd, capture_output, timeout):
        calls.append(cmd)
        tmpdir = Path(cmd[cmd.index("-output-directory") + 1])
        if pdf_bytes is not None:
            (tmpdir / "cv.pdf").write_bytes(pdf_bytes)
        if log_text is not None:
            (tmpdir / "cv.log").write_text(log_text, encoding="utf-8")
        return SimpleNamespace(
            returncode=next(codes),
            stdout=b"captured stdout",
            stderr=b"captured stderr",
        )

    monkeypatch.setattr(compile_mod.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("returncodes", [(1, 0), (0, 1)])
def test_compile_latex_rejects_either_failed_pass(monkeypatch, returncodes):
    calls = _fake_xelatex(
        monkeypatch,
        returncodes,
        log_text="! Undefined control sequence.\nOutput written on cv.pdf (1 page, 3 bytes).",
    )

    result = _compile_latex("\\documentclass{x}\\begin{document}x\\end{document}", cv_phone="")

    assert len(calls) == 2
    assert result.ok is False
    assert result.pdf_bytes is None
    assert result.page_count == 1
    assert result.repairable is True
    assert "Undefined control sequence" in result.error_excerpt


@pytest.mark.parametrize(
    ("pdf_bytes", "log_text", "page_count"),
    [
        (None, "Output written on cv.pdf (1 page, 3 bytes).", 1),
        (b"", "Output written on cv.pdf (1 page, 0 bytes).", 1),
        (b"PDF", "no output line", None),
        (b"PDF", "Output written on cv.pdf (0 pages, 3 bytes).", 0),
    ],
)
def test_compile_latex_rejects_unverifiable_pdf(monkeypatch, pdf_bytes, log_text, page_count):
    _fake_xelatex(monkeypatch, (0, 0), pdf_bytes=pdf_bytes, log_text=log_text)

    result = _compile_latex("source", cv_phone="")

    assert result.ok is False
    assert result.pdf_bytes is None
    assert result.page_count == page_count
    assert result.repairable is False


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FileNotFoundError("xelatex"), "not found"),
        (subprocess.TimeoutExpired(["xelatex"], 120), "timed out"),
    ],
)
def test_compile_latex_environment_failures_are_not_repairable(monkeypatch, exc, expected):
    monkeypatch.setattr(compile_mod.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(exc))

    result = _compile_latex("source", cv_phone="")

    assert result.ok is False
    assert result.repairable is False
    assert expected in result.error_excerpt


def test_compile_with_fixes_does_not_repair_nonrepairable_failure(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex: CompileResult(False, None, "page count unavailable", None, False),
    )
    client = fake_llm(["unused"])

    assert compile_with_fixes(client, "source") == (False, None, "source")
    assert client.prompts == []


def test_compile_with_fixes_repairs_compiler_failure(monkeypatch, fake_llm):
    results = iter(
        [
            CompileResult(False, None, "undefined control sequence", None, True),
            CompileResult(True, b"PDF", "", 1, False),
        ]
    )
    monkeypatch.setattr(compile_mod, "_compile_latex", lambda _tex: next(results))
    client = fake_llm(["fixed source"])

    assert compile_with_fixes(client, "broken source") == (True, b"PDF", "fixed source")
    assert len(client.prompts) == 1


def test_compile_with_fixes_accepts_known_one_page_result(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex: CompileResult(True, b"PDF", "", 1, False),
    )

    assert compile_with_fixes(fake_llm([]), "source") == (True, b"PDF", "source")


def test_compile_with_fixes_rejects_unrecoverable_multi_page_result(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex: CompileResult(True, b"TWO", "", 2, False),
    )
    monkeypatch.setattr(
        onepage_mod,
        "_shrink_to_one_page",
        lambda tex, pdf, pages: (b"STILL_TWO", "shrunk", 2),
    )

    assert compile_with_fixes(fake_llm([]), "source") == (False, None, "shrunk")


def test_shrink_to_one_page_uses_verified_compile_result(monkeypatch):
    results = iter(
        [
            CompileResult(False, None, "broken", None, True),
            CompileResult(True, b"ONE", "", 1, False),
        ]
    )
    monkeypatch.setattr(onepage_mod, "_compile_latex", lambda _tex: next(results))

    source = "\\documentclass[10pt]{article}\\begin{document}x\\end{document}"
    pdf, final_tex, pages = onepage_mod._shrink_to_one_page(source, b"TWO", 2)

    assert pdf == b"ONE"
    assert final_tex != source
    assert pages == 1


# =====================================================================
# audit order 8 — XeLaTeX worker limit (module-level semaphore)
# =====================================================================


class _RecordingSemaphore:
    """Context-manager stand-in for compile._XELATEX_SEMAPHORE.

    Records enters/exits and the current hold depth so a test can prove
    _compile_latex holds it (exactly once) around BOTH xelatex passes.
    Deterministic: no real threads, blocking, or sleeps involved.
    """

    def __init__(self):
        self.enters = 0
        self.exits = 0
        self.depth = 0
        self.max_depth = 0

    def __enter__(self):
        self.enters += 1
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        return self

    def __exit__(self, *_exc):
        self.exits += 1
        self.depth -= 1
        return False


def test_compile_latex_holds_xelatex_semaphore_around_both_passes(monkeypatch):
    sem = _RecordingSemaphore()
    # raising=True (default): fails until compile.py grows _XELATEX_SEMAPHORE.
    monkeypatch.setattr(compile_mod, "_XELATEX_SEMAPHORE", sem)

    depth_at_each_run = []

    def run(cmd, capture_output, timeout):
        # both xelatex passes must observe the semaphore held (depth == 1)
        depth_at_each_run.append(sem.depth)
        tmpdir = Path(cmd[cmd.index("-output-directory") + 1])
        (tmpdir / "cv.pdf").write_bytes(b"PDF")
        (tmpdir / "cv.log").write_text(
            "Output written on cv.pdf (1 page, 3 bytes).", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(compile_mod.subprocess, "run", run)

    result = _compile_latex(
        "\\documentclass{x}\\begin{document}x\\end{document}", cv_phone=""
    )

    assert result.ok is True
    # entered exactly once, wrapping BOTH passes, and released afterward
    assert sem.enters == 1
    assert sem.exits == 1
    assert depth_at_each_run == [1, 1]
    assert sem.max_depth == 1
    assert sem.depth == 0


def test_xelatex_semaphore_exists_and_is_a_real_semaphore():
    import threading

    sem = compile_mod._XELATEX_SEMAPHORE
    # a real threading.Semaphore at module scope bounds concurrent xelatex runs
    assert isinstance(sem, threading.Semaphore)
