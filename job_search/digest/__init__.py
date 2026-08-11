"""Run digest: a single ZIP (HTML dashboard + tailored CVs) per pipeline run."""
from .bundle import build_digest_zip, cv_filename_for, digest_filename
from .fixtures import oversized_context, sample_context
from .model import DeferredEntry, DigestContext, FitEntry, ReviewEntry
from .publish import publish_digest, retract_digest
from .render import render_digest_html
from .section_config import load_sections
from .sections import Section, group_entries
from .telegraph import (
    INDEX_TITLE, content_size, digest_page_title, render_digest_nodes, render_index_nodes,
)

__all__ = [
    "DeferredEntry",
    "DigestContext",
    "FitEntry",
    "INDEX_TITLE",
    "ReviewEntry",
    "Section",
    "build_digest_zip",
    "content_size",
    "cv_filename_for",
    "digest_filename",
    "digest_page_title",
    "group_entries",
    "load_sections",
    "oversized_context",
    "publish_digest",
    "render_digest_html",
    "render_digest_nodes",
    "render_index_nodes",
    "retract_digest",
    "sample_context",
]
