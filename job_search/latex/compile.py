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


def _compile_latex(tex_source: str, cv_phone=None) -> tuple:
    """
    Compile tex_source with xelatex.
    Returns (success: bool, pdf_bytes: bytes|None, error_excerpt: str,
             page_count: int|None).

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
            # Run twice so cross-references resolve.
            for _ in range(2):
                subprocess.run(cmd, capture_output=True, timeout=120)

            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return True, f.read(), "", pdf_pages_from_log(log_path)

            error_excerpt = ""
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    error_excerpt = _extract_latex_errors(f.read())
            return False, None, error_excerpt, None

    except FileNotFoundError:
        return False, None, "xelatex not found — cannot compile PDF", None


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
        success, pdf_bytes, error_excerpt, pages = _compile_latex(tex_source)
        if success:
            if pages is not None and pages > 1:
                pdf_bytes, tex_source, pages = _shrink_to_one_page(tex_source, pdf_bytes, pages)
            return True, pdf_bytes, tex_source
        print(f"    Compilation failed (attempt {attempt}/{max_attempts}): {error_excerpt[:120]}", flush=True)
        if attempt < max_attempts:
            print(f"    Asking Gemini to fix LaTeX errors...", flush=True)
            try:
                tex_source = _fix_latex(client, tex_source, error_excerpt)
            except Exception as exc:
                print(f"    Fix request failed: {exc}", file=sys.stderr)
                break
    return False, None, tex_source
