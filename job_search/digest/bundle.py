"""Assemble the run digest into a single in-memory ZIP (stdlib only).

Layout inside the archive:
    index.html          the dashboard (render.py)
    cvs/<name>.pdf       one tailored CV per fit, linked from index.html

CV filenames are ``igor_pivnyk_cv_<company>.pdf`` — identical to what the legacy
per-job path sends (pipeline/stages.py), so both delivery paths finally agree on
what a tailored CV is called. They are made unique within the archive so two
fits at the same company never collide and overwrite each other; the same name
string is written into the HTML link, so the local "Download CV" links always
resolve after the user extracts the archive.

The name used to carry a six-hex SHA-256 prefix of the job URL. It is now also
the name a human downloads from the digest page and forwards to a recruiter, and
the hash read as noise on such a file; the uniqueness it provided is already
covered by the numeric suffix below.
"""
import io
import re
import zipfile

from ..models import coerce_job
from .render import render_digest_html


def _company_slug(company) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(company or "").lower()).strip("_")
    return slug or "unknown"


def cv_filename_for(job, taken) -> str:
    """A collision-free ``igor_pivnyk_cv_<company>.pdf`` name.

    ``taken`` is the set of names already used in this run; a numeric suffix is
    appended when the company repeats, so two fits at Acme give
    ``igor_pivnyk_cv_acme.pdf`` and ``igor_pivnyk_cv_acme_2.pdf``.
    """
    job = coerce_job(job)
    base = "igor_pivnyk_cv_{}".format(_company_slug(job.get("company", "")))
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


def build_encrypted_cv_zip(entries, password: str) -> bytes:
    """Return one AES-256 ZIP whose members are ordinary tailored PDFs."""
    import pyzipper

    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for entry in entries:
            if entry.pdf_bytes:
                archive.writestr(entry.cv_filename, entry.pdf_bytes)
    return buffer.getvalue()


def build_digest_zip(ctx, rendered_html=None) -> bytes:
    """Render the dashboard and bundle every generated tailored CV."""
    html = render_digest_html(ctx) if rendered_html is None else rendered_html
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html)
        for entry in list(ctx.fits) + list(ctx.review):
            if entry.pdf_bytes:
                archive.writestr("cvs/{}".format(entry.cv_filename), entry.pdf_bytes)
    return buffer.getvalue()
