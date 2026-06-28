"""Deterministic one-page auto-shrink ladder for tailored CVs."""
import re
import sys

from .compile import _compile_latex

# Progressive density steps, gentlest first. Each step holds ABSOLUTE target
# values (applied to the original tailored .tex, never stacked) so the first
# step that yields a single page is the least visually disruptive one. A
# tailored CV that overflows by a line or two normally fits again within the
# first couple of steps; the tighter steps are a guaranteed-convergence backstop.
ONE_PAGE_SHRINK_LADDER = [
    {"font": 9.5, "stretch": 0.88, "itemsep": 0.5, "topsep": 1, "sec_before": 2, "sec_after": 1, "top": 0.7, "bottom": 0.5, "arraystretch": 1.0},
    {"font": 9.5, "stretch": 0.86, "itemsep": 0,   "topsep": 1, "sec_before": 1, "sec_after": 1, "top": 0.7, "bottom": 0.5, "arraystretch": 0.97},
    {"font": 9.5, "stretch": 0.84, "itemsep": 0,   "topsep": 0.5, "sec_before": 1, "sec_after": 1, "top": 0.6, "bottom": 0.5, "arraystretch": 0.95},
    {"font": 9,   "stretch": 0.86, "itemsep": 0,   "topsep": 1, "sec_before": 1, "sec_after": 1, "top": 0.6, "bottom": 0.5, "arraystretch": 0.95},
    {"font": 9,   "stretch": 0.83, "itemsep": 0,   "topsep": 0.5, "sec_before": 1, "sec_after": 0, "top": 0.6, "bottom": 0.4, "arraystretch": 0.92},
    {"font": 8.5, "stretch": 0.82, "itemsep": 0,   "topsep": 0,  "sec_before": 1, "sec_after": 0, "top": 0.5, "bottom": 0.4, "arraystretch": 0.9},
]


def _apply_density_overrides(tex_source: str, step: dict) -> str:
    """Return tex_source re-tuned to `step`'s density, for the one-page guard.

    Lowers the \\documentclass font and appends an override block as the last
    thing before \\begin{document}. Because every knob (\\geometry, \\setstretch,
    \\titlespacing, \\setlist, \\arraystretch) is re-issued AFTER the template's
    own settings, the override wins — no need to know the template's values. All
    referenced packages (geometry, setspace, titlesec, enumitem) are already
    loaded by the base preamble the tailored CV is built from.
    """
    # Lower the main font size in the documentclass options (e.g. 9.5pt -> 9pt).
    tex_source = re.sub(
        r"(\\documentclass\[[^\]]*?)(\d+(?:\.\d+)?)pt",
        lambda m: f"{m.group(1)}{step['font']:g}pt",
        tex_source,
        count=1,
    )
    override = (
        "% --- one-page guard: auto-shrink density overrides (pipeline) ---\n"
        f"\\geometry{{top={step['top']:g}cm,bottom={step['bottom']:g}cm}}\n"
        f"\\setstretch{{{step['stretch']:g}}}\n"
        f"\\titlespacing*{{\\section}}{{0pt}}{{{step['sec_before']:g}pt}}{{{step['sec_after']:g}pt}}\n"
        f"\\setlist[itemize]{{itemsep={step['itemsep']:g}pt,topsep={step['topsep']:g}pt,parsep=0pt}}\n"
        f"\\renewcommand{{\\arraystretch}}{{{step['arraystretch']:g}}}\n"
    )
    if "\\begin{document}" in tex_source:
        return tex_source.replace("\\begin{document}", override + "\\begin{document}", 1)
    return tex_source  # malformed; leave untouched so the caller keeps the original


def _shrink_to_one_page(tex_source: str, pdf_bytes: bytes, page_count: int) -> tuple:
    """Force a compiled-but-overflowing CV down to a single page.

    Walks ONE_PAGE_SHRINK_LADDER (gentlest first), recompiling each candidate,
    and returns the first that renders as exactly one page. If none reaches one
    page, returns the fewest-page candidate seen (or the original). Returns
    (pdf_bytes, final_tex, page_count).
    """
    print(f"    PDF is {page_count} pages — auto-shrinking density to fit one page...", flush=True)
    best = (pdf_bytes, tex_source, page_count)
    for i, step in enumerate(ONE_PAGE_SHRINK_LADDER, 1):
        candidate = _apply_density_overrides(tex_source, step)
        ok, cand_pdf, _err, pages = _compile_latex(candidate)
        if not ok or pages is None:
            continue
        if pages < best[2]:
            best = (cand_pdf, candidate, pages)
        if pages == 1:
            print(f"    Auto-shrink fit one page at step {i}/{len(ONE_PAGE_SHRINK_LADDER)}.", flush=True)
            return cand_pdf, candidate, 1
    print(
        f"    Auto-shrink could not reach one page (best {best[2]} pages) — "
        f"delivering tightest version, REVIEW BEFORE SENDING.",
        file=sys.stderr,
    )
    return best
