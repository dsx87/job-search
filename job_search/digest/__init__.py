"""Run digest: a single ZIP (HTML dashboard + tailored CVs) per pipeline run."""
from .bundle import build_digest_zip, cv_filename_for, digest_filename
from .model import DeferredEntry, DigestContext, FitEntry, ReviewEntry
from .render import render_digest_html

__all__ = [
    "DeferredEntry",
    "DigestContext",
    "FitEntry",
    "ReviewEntry",
    "build_digest_zip",
    "cv_filename_for",
    "digest_filename",
    "render_digest_html",
]
