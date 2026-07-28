"""Data model for one run's digest, consumed by render.py and bundle.py.

These are plain presentation records assembled by the pipeline after the
tailor stage. They deliberately hold the *structured* job/evaluation objects
(not pre-rendered strings) so the HTML renderer can decide the layout.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FitEntry:
    """A job judged a fit, with its tailored CV bundled into the ZIP."""

    job: object
    evaluation: Optional[dict]
    summary: str
    pdf_bytes: bytes
    cv_filename: str


@dataclass
class ReviewEntry:
    """A job the policy could not confidently decide (surfaced for review)."""

    job: object
    evaluation: dict
    summary: str = ""


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
