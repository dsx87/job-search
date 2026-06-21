#!/usr/bin/env python3
"""Render the base CV (igor_pivnyk_cv_base_updated.tex) to PDF with xelatex.

The ((PHONE)) placeholder is substituted from the CV_PHONE environment variable
at compile time, mirroring pipeline._compile_latex. When CV_PHONE is unset the
placeholder collapses to nothing, producing the masked sample committed to the
public repo. Set CV_PHONE locally to render a full copy for yourself.
"""
import os
import shutil
import subprocess
import sys
import tempfile

BASE_TEX_FILE = "igor_pivnyk_cv_base_updated.tex"
OUT_PDF_FILE = "igor_pivnyk_cv_base_updated.pdf"


def main() -> int:
    with open(BASE_TEX_FILE, encoding="utf-8") as f:
        tex_source = f.read()

    phone = os.environ.get("CV_PHONE", "").strip()
    phone_tex = f"\\enspace\\textbar\\enspace {phone}" if phone else ""
    tex_source = tex_source.replace("((PHONE))", phone_tex)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "cv.tex")
        pdf_path = os.path.join(tmpdir, "cv.pdf")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)

        cmd = ["xelatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path]
        for _ in range(2):  # twice so cross-references resolve
            subprocess.run(cmd, capture_output=True, timeout=120)

        if not os.path.exists(pdf_path):
            print("xelatex did not produce a PDF — see the log above.", file=sys.stderr)
            return 1

        shutil.copyfile(pdf_path, OUT_PDF_FILE)

    print(f"Wrote {OUT_PDF_FILE} (phone {'included' if phone else 'masked'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
