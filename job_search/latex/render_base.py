"""Render a configured candidate base CV to a verified PDF.

Run with: python -m job_search.latex.render_base
"""
import os
import sys

from ..config import ConfigurationError, PipelineConfig
from ..runtime import build_runtime


def main(cfg=None) -> int:
    try:
        cfg = PipelineConfig.from_env() if cfg is None else cfg
    except (ValueError, OSError) as exc:
        print("ERROR: base CV rendering failed: {}".format(exc), file=sys.stderr)
        return 1
    base_path = str(getattr(cfg, "base_tex_file", "") or "").strip()
    output_path = str(getattr(cfg, "rendered_base_file", "") or "").strip()
    if not base_path or not output_path:
        print(
            "ERROR: base CV rendering requires configured base_tex_file and rendered_base_file.",
            file=sys.stderr,
        )
        return 1
    try:
        rt = build_runtime(cfg, command="base")
    except ConfigurationError as exc:
        print("ERROR: base CV rendering failed: {}".format(exc), file=sys.stderr)
        return 1
    try:
        artifact = rt.cv_renderer.render_base(rt.llm)
    except Exception as exc:
        print("ERROR: base CV rendering failed: {}".format(exc), file=sys.stderr)
        return 1
    try:
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as handle:
            handle.write(artifact.content)
        manifest = os.environ.get("JOB_SEARCH_RENDER_BASE_MANIFEST", "").strip()
        if manifest:
            with open(manifest, "w", encoding="utf-8") as handle:
                handle.write(os.path.abspath(output_path) + "\n")
    except OSError as exc:
        print("ERROR: base CV output write failed: {}".format(exc), file=sys.stderr)
        return 1

    if artifact.media_type == "application/pdf":
        detail = "verified PDF"
    else:
        detail = artifact.media_type
    print("Wrote {} ({}).".format(output_path, detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
