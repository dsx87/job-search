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
from ..config import PipelineConfig


def main(cfg=None) -> int:
    cfg = PipelineConfig.from_env() if cfg is None else cfg
    components = load_components(cfg, command="base")
    try:
        artifact = components.cv_renderer.render_base(components.llm)
    except Exception as exc:
        print("ERROR: base CV rendering failed: {}".format(exc), file=sys.stderr)
        return 1
    output_path = cfg.rendered_base_file
    with open(output_path, "wb") as handle:
        handle.write(artifact.content)
    manifest = os.environ.get("JOB_SEARCH_RENDER_BASE_MANIFEST", "").strip()
    if manifest:
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write(os.path.abspath(output_path) + "\n")

    phone = os.environ.get("CV_PHONE", "").strip()
    if artifact.media_type == "application/pdf":
        detail = "1 page, phone {}".format("included" if phone else "masked")
    else:
        detail = artifact.media_type
    print("Wrote {} ({}).".format(output_path, detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
