"""Data model for one run's digest, consumed by render.py and bundle.py.

These are plain presentation records assembled by the pipeline after the
tailor stage. They deliberately hold the *structured* job/evaluation objects
(not pre-rendered strings) so the HTML renderer can decide the layout.
"""
from dataclasses import dataclass, field
from typing import Optional

from ..components import CVArtifact


@dataclass
class FitEntry:
    """A job judged a fit, with its tailored CV bundled into the ZIP."""

    job: object
    evaluation: Optional[dict]
    summary: str
    pdf_bytes: bytes = b""
    cv_filename: str = ""
    # Retained for compatibility with older fixture/custom-context callers.
    # Production Telegraph pages intentionally publish only the archive URL.
    cv_url: str = ""
    artifact: Optional[CVArtifact] = None

    def __post_init__(self):
        if self.artifact is None and (self.pdf_bytes or self.cv_filename):
            self.artifact = CVArtifact(
                self.cv_filename, "application/pdf", self.pdf_bytes
            )
        elif self.artifact is not None:
            self.pdf_bytes = self.artifact.content
            self.cv_filename = self.artifact.filename


@dataclass
class ReviewEntry:
    """A job the policy could not confidently decide (surfaced for review)."""

    job: object
    evaluation: dict
    summary: str = ""
    pdf_bytes: bytes = b""
    cv_filename: str = ""
    artifact: Optional[CVArtifact] = None

    def __post_init__(self):
        if self.artifact is None and (self.pdf_bytes or self.cv_filename):
            self.artifact = CVArtifact(
                self.cv_filename, "application/pdf", self.pdf_bytes
            )
        elif self.artifact is not None:
            self.pdf_bytes = self.artifact.content
            self.cv_filename = self.artifact.filename


@dataclass
class DeferredEntry:
    """A job with too little description text to evaluate (retries next run)."""

    job: object


@dataclass
class DigestContext:
    """Everything the dashboard needs for a single run."""

    date: object
    stats: object
    source_warning: str
    usage_summary: str
    fits: list = field(default_factory=list)
    review: list = field(default_factory=list)
    deferred: list = field(default_factory=list)
    # User-defined sections (see digest.sections) and anything the loader wants
    # the reader to know about them. Both empty → today's ungrouped dashboard.
    sections: tuple = ()
    sections_error: str = ""
    # The "download all CVs" archive, and whether the uploaded files are
    # password-protected. The password itself is deliberately NOT here: the
    # renderer publishes this context to a public page, so it must not be able
    # to reach the one secret that keeps those uploads private.
    cv_zip_url: str = ""
    cv_encrypted: bool = False
