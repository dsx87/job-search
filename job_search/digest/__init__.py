"""Run digest: a single ZIP (HTML dashboard + tailored CVs) per pipeline run."""
from .bundle import build_digest_zip, cv_filename_for, digest_filename
from .model import DeferredEntry, DigestContext, FitEntry, ReviewEntry
from .render import render_digest_html
from .section_config import load_sections
from .sections import Section, group_entries

__all__ = [
    "DeferredEntry",
    "DigestContext",
    "FitEntry",
    "ReviewEntry",
    "Section",
    "build_digest_zip",
    "cv_filename_for",
    "digest_filename",
    "group_entries",
    "load_sections",
    "render_digest_html",
]
