"""Render the base CV (igor_pivnyk_cv_base_updated.tex) to PDF with pdflatex.

The ((PHONE)) placeholder is substituted from the CV_PHONE environment variable
at compile time, mirroring latex.compile._compile_latex. When CV_PHONE is unset
the placeholder collapses to nothing, producing the masked sample committed to
the public repo. Set CV_PHONE locally to render a full copy for yourself.

Run with: python -m job_search.latex.render_base
"""
import os
import shutil
import subprocess
import sys
import tempfile

from ..composition import load_components
from ..config import BASE_TEX_FILE, OUT_PDF_FILE, PipelineConfig
from .compile import pdf_pages_from_log


def main(cfg=None) -> int:
    cfg = PipelineConfig.from_env() if cfg is None else cfg
    load_components(cfg, command="base")
    base_tex_file = getattr(cfg, "base_tex_file", BASE_TEX_FILE)
    out_pdf_file = getattr(cfg, "rendered_base_file", OUT_PDF_FILE)
    with open(base_tex_file, encoding="utf-8") as f:
        tex_source = f.read()

    phone = os.environ.get("CV_PHONE", "").strip()
    phone_tex = f"\\enspace\\textbar\\enspace {phone}" if phone else ""
    tex_source = tex_source.replace("((PHONE))", phone_tex)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "cv.tex")
        pdf_path = os.path.join(tmpdir, "cv.pdf")
        log_path = os.path.join(tmpdir, "cv.log")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)

        cmd = ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path]
        for _ in range(2):  # twice so cross-references resolve
            subprocess.run(cmd, capture_output=True, timeout=120)

        if not os.path.exists(pdf_path):
            print("pdflatex did not produce a PDF — see the log above.", file=sys.stderr)
            return 1

        # Hard guard: the base CV must be exactly one page. Fail loudly on a
        # regression so CI/the committer notices instead of shipping a 2-page
        # sample. (The tailored pipeline auto-shrinks; the hand-tuned base does
        # not — a spill here means the .tex needs a real fix.)
        pages = pdf_pages_from_log(log_path)
        if pages is not None and pages != 1:
            print(
                f"ERROR: base CV rendered {pages} pages — it must be exactly 1. "
                f"Tighten igor_pivnyk_cv_base_updated.tex (density/content) until it fits.",
                file=sys.stderr,
            )
            return 1

        shutil.copyfile(pdf_path, out_pdf_file)

    pagedesc = "1 page" if pages == 1 else f"{pages} pages" if pages else "unknown page count"
    print(f"Wrote {out_pdf_file} ({pagedesc}, phone {'included' if phone else 'masked'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
