"""xelatex compilation with self-healing fix attempts.

pdf_pages_from_log is the single consolidated page-count parser (previously
duplicated between the pipeline and the base-CV renderer). _compile_latex grows
an explicit cv_phone seam: when cv_phone is None (the default) it reads CV_PHONE
from the environment exactly as before, so production behavior is unchanged.
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass

from ..config import XELATEX_MAX_WORKERS

# Bound concurrent xelatex processes across the whole process (the tailor thread
# pool may call _compile_latex from many workers at once). Independent of
# TAILOR_WORKERS so a large pool can't spawn many parallel compilations.
_XELATEX_SEMAPHORE = threading.Semaphore(XELATEX_MAX_WORKERS)


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    pdf_bytes: "bytes | None"
    error_excerpt: str
    page_count: "int | None"
    repairable: bool


def _strip_latex_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:latex)?\n(.*)\n```$", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Extract from \documentclass to \end{document} to drop any surrounding prose
    m = re.search(r'(\\documentclass.*?\\end\{document\})', text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _extract_latex_errors(log: str) -> str:
    """Pull error blocks (lines starting with '!') from an xelatex log."""
    lines = log.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("!"):
            block = lines[i : i + 10]
            blocks.append("\n".join(block))
            i += 10
        else:
            i += 1
    if blocks:
        return "\n\n".join(blocks[:5])
    # Fallback: last 40 lines usually contain the relevant failure context.
    return "\n".join(lines[-40:])


def pdf_pages_from_log(log_path: str) -> "int | None":
    """Parse the page count from an xelatex log.

    xelatex/pdftex emits a stable line like
    ``Output written on cv.pdf (2 pages, 34567 bytes).`` — this is the only
    dependency-free way to count pages, since XeLaTeX PDFs pack the page tree
    into compressed object streams that a naive byte scan can't read. Returns
    None when the line is absent (e.g. the compile produced no PDF).
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError:
        return None
    m = re.search(r"Output written on [^\n(]*\((\d+)\s+pages?", log)
    return int(m.group(1)) if m else None


def _decode_output(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _compiler_error_excerpt(log_path: str, completed) -> str:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError:
        log = ""
    if log:
        return _extract_latex_errors(log)
    captured = []
    for process in completed:
        captured.extend((_decode_output(process.stderr), _decode_output(process.stdout)))
    return "\n".join(part for part in captured if part).strip()


def _compile_latex(tex_source: str, cv_phone=None) -> CompileResult:
    """
    Compile tex_source with xelatex.
    Returns a CompileResult. A result is successful only when both compiler
    passes exit cleanly and produce a nonempty PDF with a known positive page
    count.

    cv_phone=None (default) reads the real number from the CV_PHONE environment
    variable, kept out of the repo and the LLM prompt; pass a string to inject
    one explicitly. When empty the ((PHONE)) token collapses to nothing.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "cv.tex")
            pdf_path = os.path.join(tmpdir, "cv.pdf")
            log_path = os.path.join(tmpdir, "cv.log")

            # Inject the real phone number (kept out of the repo and out of the
            # LLM prompt) only at compile time. When CV_PHONE is unset the token
            # collapses to nothing, leaving no dangling separator.
            phone = (os.environ.get("CV_PHONE", "") if cv_phone is None else cv_phone).strip()
            phone_tex = f"\\enspace\\textbar\\enspace {phone}" if phone else ""
            tex_source = tex_source.replace("((PHONE))", phone_tex)

            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(tex_source)

            cmd = ["xelatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path]
            # Run twice so cross-references resolve. Hold the shared xelatex
            # semaphore across both passes to cap concurrent compilations.
            completed = []
            with _XELATEX_SEMAPHORE:
                for _ in range(2):
                    completed.append(subprocess.run(cmd, capture_output=True, timeout=120))

            pages = pdf_pages_from_log(log_path)
            error_excerpt = _compiler_error_excerpt(log_path, completed)
            if any(process.returncode != 0 for process in completed):
                return CompileResult(False, None, error_excerpt, pages, True)

            pdf_bytes = None
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
            if not pdf_bytes:
                detail = error_excerpt or "xelatex did not produce a nonempty PDF"
                return CompileResult(False, None, detail, pages, False)
            if pages is None or pages <= 0:
                detail = error_excerpt or "xelatex PDF page count could not be verified"
                return CompileResult(False, None, detail, pages, False)
            return CompileResult(True, pdf_bytes, "", pages, False)

    except FileNotFoundError:
        return CompileResult(False, None, "xelatex not found — cannot compile PDF", None, False)
    except subprocess.TimeoutExpired:
        return CompileResult(False, None, "xelatex timed out — cannot verify PDF", None, False)


def _fix_latex(client, tex_source: str, error_excerpt: str) -> str:
    """Ask the model to fix a broken LaTeX source given the compiler error."""
    prompt = f"""The LaTeX source below failed to compile with xelatex. Fix the compilation errors.

## Compiler errors

{error_excerpt}

## Broken LaTeX source

{tex_source}

Fix only what is broken. Do not change the content or layout. \
Output the complete fixed .tex file — raw LaTeX only, no markdown fences, no explanation.
"""
    raw = client.generate(prompt, temperature=0.0)
    return _strip_latex_fences(raw)


def compile_with_fixes(client, tex_source: str, max_attempts: int = 3) -> tuple:
    """
    Try to compile tex_source, asking the model to fix errors between attempts.
    On a successful compile that overflows past one page, deterministically
    shrink the layout until it fits (see _shrink_to_one_page).
    Returns (success: bool, pdf_bytes: bytes|None, final_tex: str).
    """
    from .onepage import _shrink_to_one_page

    for attempt in range(1, max_attempts + 1):
        result = _compile_latex(tex_source)
        if result.ok:
            if result.page_count == 1 and result.pdf_bytes:
                return True, result.pdf_bytes, tex_source
            if result.page_count and result.page_count > 1:
                pdf_bytes, tex_source, pages = _shrink_to_one_page(
                    tex_source, result.pdf_bytes, result.page_count
                )
                if pages == 1 and pdf_bytes:
                    return True, pdf_bytes, tex_source
            return False, None, tex_source
        print(
            f"    Compilation failed (attempt {attempt}/{max_attempts}): "
            f"{result.error_excerpt[:120]}",
            flush=True,
        )
        if result.repairable and attempt < max_attempts:
            print(f"    Asking Gemini to fix LaTeX errors...", flush=True)
            try:
                tex_source = _fix_latex(client, tex_source, result.error_excerpt)
            except Exception as exc:
                print(f"    Fix request failed: {exc}", file=sys.stderr)
                break
        else:
            break
    return False, None, tex_source
