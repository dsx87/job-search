#!/usr/bin/env python3
"""CI self-test for the single-page CV guard (requires xelatex + Carlito).

Verifies the two halves of the one-page guarantee:
  1. The hand-tuned base CV compiles to exactly one page.
  2. pipeline._shrink_to_one_page deterministically pulls an overflowing CV
     back to exactly one page.

The overflow in (2) is manufactured by *loosening* the base's density (bigger
font, looser spacing) without changing any content — so the same content is
known to fit at tight density, and the shrink ladder is guaranteed to be able
to recover it. Exits non-zero on any failure so CI fails loudly.
"""
import re
import sys

import pipeline

BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"


def _blow_up(tex: str) -> str:
    """Force the one-page base past a page via loose density only (no content
    change), mimicking the 'one line on page 2' overflow the guard must fix."""
    tex = re.sub(
        r"(\\documentclass\[[^\]]*?)(\d+(?:\.\d+)?)pt",
        lambda m: f"{m.group(1)}12pt",
        tex,
        count=1,
    )
    blow = (
        "\\geometry{top=2.2cm,bottom=2.2cm}\n"
        "\\setstretch{1.3}\n"
        "\\setlist[itemize]{itemsep=6pt,topsep=8pt}\n"
    )
    return tex.replace("\\begin{document}", blow + "\\begin{document}", 1)


def main() -> int:
    with open(BASE_TEX_FILE, encoding="utf-8") as f:
        tex = f.read()

    # 1. Base CV must be exactly one page.
    ok, _pdf, err, pages = pipeline._compile_latex(tex)
    if not ok:
        print(f"FAIL: base CV did not compile: {err[:200]}", file=sys.stderr)
        return 1
    if pages != 1:
        print(f"FAIL: base CV is {pages} pages, expected exactly 1.", file=sys.stderr)
        return 1
    print("PASS: base CV compiles to exactly 1 page.")

    # 2a. The blown-up CV must actually overflow (otherwise the test is vacuous).
    blown = _blow_up(tex)
    ok, pdf, err, pages = pipeline._compile_latex(blown)
    if not ok:
        print(f"FAIL: blown-up CV did not compile: {err[:200]}", file=sys.stderr)
        return 1
    if pages is None or pages < 2:
        print(f"FAIL: blow-up did not overflow (got {pages} pages) — test invalid.", file=sys.stderr)
        return 1
    print(f"PASS: blown-up CV overflows to {pages} pages.")

    # 2b. Auto-shrink must bring it back to exactly one page.
    _pdf2, _final_tex, final_pages = pipeline._shrink_to_one_page(blown, pdf, pages)
    if final_pages != 1:
        print(f"FAIL: auto-shrink ended at {final_pages} pages, expected 1.", file=sys.stderr)
        return 1
    print("PASS: auto-shrink reduced the overflow back to exactly 1 page.")

    print("ALL ONE-PAGE GUARD CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
