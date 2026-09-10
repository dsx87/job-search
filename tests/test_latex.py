"""Characterization tests for LaTeX string ops and the CV validator."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

# --- modules under test (repoint on migration) ---
from job_search.profile import validate_tailored_cv
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


EMPLOYERS = ("Example Labs", "Sample Systems")


def _cv(order=EMPLOYERS, extra=""):
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
    assert validate_tailored_cv(_cv(), expected_job_order=EMPLOYERS) == []


def test_validate_tailored_cv_out_of_order():
    v = validate_tailored_cv(
        _cv(order=("Sample Systems", "Example Labs")), expected_job_order=EMPLOYERS
    )
    assert len(v) == 1
    assert "out of order" in v[0]


def test_validate_tailored_cv_missing_job():
    v = validate_tailored_cv(_cv(order=("Example Labs",)), expected_job_order=EMPLOYERS)
    assert any("missing job" in x for x in v)


def test_validate_tailored_cv_forbidden_term():
    v = validate_tailored_cv(
        _cv(extra="A forbidden claim."), forbidden_term_patterns=(r"forbidden claim",)
    )
    assert any("forbidden term present: 'forbidden claim'" == x for x in v)


def test_validate_tailored_cv_forbidden_cpp_development():
    v = validate_tailored_cv(
        _cv(extra="Developed C++ shared libraries."),
        forbidden_term_patterns=(r"develop\w* c\+\+",),
    )
    assert any(x.startswith("forbidden term present:") and "C++" in x for x in v)


def test_validate_tailored_cv_allows_cpp_interop():
    assert validate_tailored_cv(
        _cv(extra="Used Swift/C++ interop; C++ Interop."),
        forbidden_term_patterns=(r"develop\w* c\+\+",),
    ) == []


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


def test_apply_density_overrides_inserts_block_with_tunable_macros():
    src = "\\documentclass[10pt,a4paper]{article}\n\\begin{document}\nbody\n\\end{document}"
    step = ONE_PAGE_SHRINK_LADDER[3]  # font 9 -> leading 10.5
    out = _apply_density_overrides(src, step)
    # the \documentclass is left untouched — the real body size lives in \cvbasefont
    assert "\\documentclass[10pt,a4paper]{article}" in out
    assert "\\renewcommand{\\cvbasefont}{\\fontsize{9pt}{10.5pt}\\selectfont}" in out
    assert "one-page guard" in out
    # override block is inserted immediately before \begin{document}
    assert out.index("one-page guard") < out.index("\\begin{document}")
    assert "\\setstretch{0.86}" in out
    assert "\\setlength{\\cvsecbefore}{1pt}\\setlength{\\cvsecafter}{1pt}" in out
    assert "\\setlength{\\cvitemsep}{0pt}\\setlength{\\cvtopsep}{1pt}\\setlength{\\cvparsep}{0pt}" in out
    assert "\\renewcommand{\\arraystretch}{0.95}" in out


def test_default_validator_has_no_candidate_specific_guard():
    assert validate_tailored_cv(_cv(extra="A forbidden claim.")) == []


def _fake_pdflatex(monkeypatch, returncodes, pdf_bytes=b"PDF", log_text=None):
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
    calls = _fake_pdflatex(
        monkeypatch,
        returncodes,
        log_text="! Undefined control sequence.\nOutput written on cv.pdf (1 page, 3 bytes).",
    )

    result = _compile_latex("\\documentclass{x}\\begin{document}x\\end{document}")

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
    _fake_pdflatex(monkeypatch, (0, 0), pdf_bytes=pdf_bytes, log_text=log_text)

    result = _compile_latex("source")

    assert result.ok is False
    assert result.pdf_bytes is None
    assert result.page_count == page_count
    assert result.repairable is False


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FileNotFoundError("pdflatex"), "not found"),
        (subprocess.TimeoutExpired(["pdflatex"], 120), "timed out"),
    ],
)
def test_compile_latex_environment_failures_are_not_repairable(monkeypatch, exc, expected):
    monkeypatch.setattr(compile_mod.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(exc))

    result = _compile_latex("source")

    assert result.ok is False
    assert result.repairable is False
    assert expected in result.error_excerpt


def test_compile_with_fixes_does_not_repair_nonrepairable_failure(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(False, None, "page count unavailable", None, False),
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
    monkeypatch.setattr(compile_mod, "_compile_latex", lambda _tex, **_kw: next(results))
    client = fake_llm(["fixed source"])

    assert compile_with_fixes(client, "broken source") == (True, b"PDF", "fixed source")
    assert len(client.prompts) == 1


def test_compile_with_fixes_keeps_the_explicit_limit_after_a_repair(monkeypatch, fake_llm):
    results = iter(
        [
            CompileResult(False, None, "undefined control sequence", None, True),
            CompileResult(True, b"TWO", "", 2, False),
        ]
    )
    monkeypatch.setattr(compile_mod, "_compile_latex", lambda _tex, **_kw: next(results))
    client = fake_llm(["fixed source"])

    assert compile_with_fixes(
        client, "broken source", max_pages=2, return_page_count=True
    ) == (True, b"TWO", "fixed source", 2)


def test_compile_with_fixes_accepts_known_one_page_result(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(True, b"PDF", "", 1, False),
    )

    assert compile_with_fixes(fake_llm([]), "source") == (True, b"PDF", "source")


def test_compile_with_fixes_accepts_a_verified_page_count_within_explicit_limit(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(True, b"TWO", "", 2, False),
    )

    assert compile_with_fixes(
        fake_llm([]), "source", max_pages=2, return_page_count=True
    ) == (
        True, b"TWO", "source", 2,
    )


def test_compile_with_fixes_shrinks_only_when_page_count_exceeds_explicit_limit(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(True, b"THREE", "", 3, False),
    )
    monkeypatch.setattr(
        onepage_mod,
        "_shrink_to_page_limit",
        lambda tex, pdf, pages, max_pages, **_kw: (b"TWO", "shrunk", 2),
    )

    assert compile_with_fixes(
        fake_llm([]), "source", max_pages=2, return_page_count=True
    ) == (
        True, b"TWO", "shrunk", 2,
    )


def test_compile_with_fixes_reports_the_verified_count_when_shrink_is_exhausted(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(True, b"THREE", "", 3, False),
    )
    monkeypatch.setattr(
        onepage_mod,
        "_shrink_to_page_limit",
        lambda tex, pdf, pages, max_pages, **_kw: (b"THREE", "shrunk", 3),
    )

    assert compile_with_fixes(
        fake_llm([]), "source", max_pages=2, return_page_count=True
    ) == (False, None, "shrunk", 3)


def test_compiler_preserves_the_actual_verified_page_count(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "compile_with_fixes",
        lambda *args, **kwargs: (True, b"TWO", "final", 2),
    )

    result = compile_mod.LatexCompiler().compile(fake_llm([]), "source", max_pages=2)

    assert result.ok is True
    assert result.page_count == 2
    assert result.tex_source == "final"


@pytest.mark.parametrize("max_pages", (True, False, 0, -1, 1.5, "2"))
def test_compile_base_rejects_invalid_explicit_page_limits_before_compiling(monkeypatch, max_pages):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not compile")),
    )

    with pytest.raises(ValueError, match="max_pages"):
        compile_mod.LatexCompiler().compile_base("source", max_pages=max_pages)


@pytest.mark.parametrize("page_count", (None, 0, -1, True, False))
def test_compile_base_rejects_invalid_verified_page_counts(monkeypatch, page_count):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda *_args, **_kwargs: CompileResult(True, b"PDF", "", page_count, False, "source"),
    )

    result = compile_mod.LatexCompiler().compile_base("source", max_pages=1)

    assert result.ok is False
    assert result.pdf_bytes is None
    assert result.page_count == page_count
    assert "verified page count" in result.error_excerpt


def test_compile_with_fixes_rejects_unrecoverable_multi_page_result(monkeypatch, fake_llm):
    monkeypatch.setattr(
        compile_mod,
        "_compile_latex",
        lambda _tex, **_kw: CompileResult(True, b"TWO", "", 2, False),
    )
    monkeypatch.setattr(
        onepage_mod,
        "_shrink_to_page_limit",
        lambda tex, pdf, pages, max_pages, **_kw: (b"STILL_TWO", "shrunk", 2),
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
# audit order 8 — pdflatex worker limit (module-level semaphore)
# =====================================================================


class _RecordingSemaphore:
    """Context-manager stand-in for compile._LATEX_SEMAPHORE.

    Records enters/exits and the current hold depth so a test can prove
    _compile_latex holds it (exactly once) around BOTH pdflatex passes.
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


def test_compile_latex_holds_latex_semaphore_around_both_passes(monkeypatch):
    sem = _RecordingSemaphore()
    # raising=True (default): fails until compile.py grows _LATEX_SEMAPHORE.
    monkeypatch.setattr(compile_mod, "_LATEX_SEMAPHORE", sem)

    depth_at_each_run = []

    def run(cmd, capture_output, timeout):
        # both pdflatex passes must observe the semaphore held (depth == 1)
        depth_at_each_run.append(sem.depth)
        tmpdir = Path(cmd[cmd.index("-output-directory") + 1])
        (tmpdir / "cv.pdf").write_bytes(b"PDF")
        (tmpdir / "cv.log").write_text(
            "Output written on cv.pdf (1 page, 3 bytes).", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(compile_mod.subprocess, "run", run)

    result = _compile_latex(
        "\\documentclass{x}\\begin{document}x\\end{document}"
    )

    assert result.ok is True
    # entered exactly once, wrapping BOTH passes, and released afterward
    assert sem.enters == 1
    assert sem.exits == 1
    assert depth_at_each_run == [1, 1]
    assert sem.max_depth == 1
    assert sem.depth == 0


def test_latex_semaphore_exists_and_is_a_real_semaphore():
    import threading

    sem = compile_mod._LATEX_SEMAPHORE
    # a real threading.Semaphore at module scope bounds concurrent pdflatex runs
    assert isinstance(sem, threading.Semaphore)
