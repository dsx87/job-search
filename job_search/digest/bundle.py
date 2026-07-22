"""Assemble the run digest into a single in-memory ZIP (stdlib only).

Layout inside the archive:
    index.html          the dashboard (render.py)
    cvs/<name>.pdf       one tailored CV per fit, linked from index.html

CV filenames are made unique so two fits at the same company never collide and
overwrite each other; the same name string is written into the HTML link, so the
local "Download CV" links always resolve after the user extracts the archive.
"""
import hashlib
import io
import re
import zipfile

from ..models import coerce_job
from .render import render_digest_html


def _company_slug(company) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(company or "").lower()).strip("_")
    return slug or "unknown"


def _short_hash(job) -> str:
    job = coerce_job(job)
    basis = str(job.get("url", "") or "").strip() or "{}|{}".format(
        job.get("title", ""), job.get("company", "")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:6]


def cv_filename_for(job, taken) -> str:
    """A collision-free ``igor_pivnyk_cv_<company>_<hash>.pdf`` name.

    ``taken`` is the set of names already used in this archive; a numeric suffix
    is appended if the hashed name somehow repeats.
    """
    job = coerce_job(job)
    base = "igor_pivnyk_cv_{}_{}".format(_company_slug(job.get("company", "")), _short_hash(job))
    name = base + ".pdf"
    counter = 2
    while name in taken:
        name = "{}_{}.pdf".format(base, counter)
        counter += 1
    return name


def digest_filename(date) -> str:
    """The archive's own filename, e.g. ``job-digest-2026-07-21.zip``."""
    iso = date.isoformat() if hasattr(date, "isoformat") else str(date)
    return "job-digest-{}.zip".format(iso)


def build_digest_zip(ctx) -> bytes:
    """Render the dashboard and bundle it with every fit's tailored CV."""
    html = render_digest_html(ctx)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html)
        for entry in ctx.fits:
            if entry.pdf_bytes:
                archive.writestr("cvs/{}".format(entry.cv_filename), entry.pdf_bytes)
    return buffer.getvalue()
