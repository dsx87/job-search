#!/usr/bin/env python3
"""CI self-test for the single-page CV guard (requires pdflatex).

Verifies the two halves of the one-page guarantee:
  1. The hand-tuned base CV compiles to exactly one page.
  2. A malformed source that still emits a PDF is rejected on nonzero exit.
  3. latex.onepage._shrink_to_one_page deterministically pulls an overflowing CV
     back to exactly one page.

The overflow in (2) is manufactured by *loosening* the base's density (bigger
font, looser spacing) without changing any content — so the same content is
known to fit at tight density, and the shrink ladder is guaranteed to be able
to recover it. Exits non-zero on any failure so CI fails loudly.

Run with: python -m job_search.tests.one_page_guard
"""
import sys

from ..config import BASE_TEX_FILE
from ..latex.compile import _compile_latex
from ..latex.onepage import _shrink_to_one_page


def _blow_up(tex: str) -> str:
    """Force the one-page base past a page via loose density only (no content
    change), mimicking the 'one line on page 2' overflow the guard must fix.

    Drives the same tunable knobs the base preamble exposes (\\cvbasefont for the
    real body size, plus the \\cvitemsep/\\cvtopsep list lengths) so the shrink
    ladder — which re-issues those same knobs after this block — can recover it."""
    blow = (
        "\\renewcommand{\\cvbasefont}{\\fontsize{13pt}{16pt}\\selectfont}\n"
        "\\geometry{top=2.2cm,bottom=2.2cm}\n"
        "\\setstretch{1.3}\n"
        "\\setlength{\\cvitemsep}{6pt}\\setlength{\\cvtopsep}{8pt}\n"
    )
    return tex.replace("\\begin{document}", blow + "\\begin{document}", 1)


def main() -> int:
    with open(BASE_TEX_FILE, encoding="utf-8") as f:
        tex = f.read()

    # 1. Base CV must be exactly one page.
    result = _compile_latex(tex)
    if not result.ok:
        print(f"FAIL: base CV did not compile: {result.error_excerpt[:200]}", file=sys.stderr)
        return 1
    if result.page_count != 1:
        print(f"FAIL: base CV is {result.page_count} pages, expected exactly 1.", file=sys.stderr)
        return 1
    print("PASS: base CV compiles to exactly 1 page.")

    # 2. Nonstop mode can emit a PDF despite compiler errors. The wrapper must
    # reject that artifact based on the nonzero return code.
    malformed = tex.replace(
        "\\end{document}",
        "\\ThisCommandDoesNotExist\n\\end{document}",
        1,
    )
    malformed_result = _compile_latex(malformed)
    if malformed_result.ok:
        print("FAIL: malformed CV with compiler errors was accepted.", file=sys.stderr)
        return 1
    if not malformed_result.page_count:
        print(
            "FAIL: malformed CV did not emit a PDF under nonstopmode — test invalid.",
            file=sys.stderr,
        )
        return 1
    print("PASS: PDF-producing nonzero compile is rejected.")

    # 3a. The blown-up CV must actually overflow (otherwise the test is vacuous).
    blown = _blow_up(tex)
    result = _compile_latex(blown)
    if not result.ok:
        print(f"FAIL: blown-up CV did not compile: {result.error_excerpt[:200]}", file=sys.stderr)
        return 1
    if result.page_count is None or result.page_count < 2:
        print(
            f"FAIL: blow-up did not overflow (got {result.page_count} pages) — test invalid.",
            file=sys.stderr,
        )
        return 1
    print(f"PASS: blown-up CV overflows to {result.page_count} pages.")

    # 3b. Auto-shrink must bring it back to exactly one page.
    _pdf2, _final_tex, final_pages = _shrink_to_one_page(
        blown, result.pdf_bytes, result.page_count
    )
    if final_pages != 1:
        print(f"FAIL: auto-shrink ended at {final_pages} pages, expected 1.", file=sys.stderr)
        return 1
    print("PASS: auto-shrink reduced the overflow back to exactly 1 page.")

    print("ALL ONE-PAGE GUARD CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
