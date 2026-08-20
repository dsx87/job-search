"""Render the base CV (igor_pivnyk_cv_base_updated.tex) to PDF with pdflatex.

The ((PHONE)) placeholder is substituted from the CV_PHONE environment variable
at compile time, mirroring latex.compile._compile_latex. When CV_PHONE is unset
the placeholder collapses to nothing, producing the masked sample committed to
the public repo. Set CV_PHONE locally to render a full copy for yourself.

Run with: python -m job_search.latex.render_base
"""
import os
import sys

from ..composition import load_components
from ..config import BASE_TEX_FILE, OUT_PDF_FILE, PipelineConfig


def main(cfg=None) -> int:
    cfg = PipelineConfig.from_env() if cfg is None else cfg
    components = load_components(cfg, command="base")
    out_pdf_file = getattr(cfg, "rendered_base_file", OUT_PDF_FILE)
    try:
        artifact = components.cv_renderer.render_base(components.llm)
    except Exception as exc:
        print("ERROR: base CV rendering failed: {}".format(exc), file=sys.stderr)
        return 1
    with open(out_pdf_file, "wb") as handle:
        handle.write(artifact.content)

    phone = os.environ.get("CV_PHONE", "").strip()
    print(f"Wrote {out_pdf_file} (1 page, phone {'included' if phone else 'masked'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
