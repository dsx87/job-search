"""Telegram/telegra.ph transport for one run's digest.

This is the *transport* half of digest delivery — encrypt-and-host the CVs,
publish the page, build the ZIP, send the single message. The bookkeeping half
(seen-state, the retry ladder, run stats) stays in ``pipeline.run``, which is
the only place that knows what a delivered fit means.

It lives here rather than in ``components.py`` so the component contracts stay
contract-shaped; ``DefaultOutputBackend.deliver_digest`` imports it lazily,
which is also what keeps ``components`` importable from ``digest.model``.
"""
import html
import sys

from ..components import DigestOutcome
from ..latex.encrypt import new_password
from ..notify.telegraph import TelegraphClient
from ..notify.x0 import X0Client
from .bundle import build_digest_zip, build_encrypted_cv_zip, digest_filename
from .publish import publish_digest, retract_digest

# Telegram's bot sendDocument ceiling. A real run bundles a handful of ~40 KB
# CVs (well under 1 MB), so this only guards a pathological batch; we log rather
# than split, since exceeding it here would mean something is badly wrong.
TELEGRAM_DOC_LIMIT = 50 * 1024 * 1024


def cv_archive_filename(date) -> str:
    """The "download all CVs" archive, e.g. ``job-cvs-2026-07-21.zip``.

    Deliberately not ``digest_filename``'s ``job-digest-<date>.zip``: that one is
    the whole digest (dashboard + CVs) sent through Telegram, this one is only
    the CVs and it is public. Two different things should not share a name.
    """
    iso = date.isoformat() if hasattr(date, "isoformat") else str(date)
    return "job-cvs-{}.zip".format(iso)


def digest_caption(n_fits, n_review, n_deferred, date, page_url="", password="") -> str:
    # Counts come from what actually went into the digest, not the run-level
    # stats (a fit whose CV failed to compile is not included).
    bits = ["{} fit{}".format(n_fits, "" if n_fits == 1 else "s")]
    if n_review:
        bits.append(f"{n_review} to review")
    if n_deferred:
        bits.append(f"{n_deferred} deferred")
    lines = ["✅ Job Search Digest — {}".format(date.isoformat())]
    if page_url and password:
        # Second line, above the counts, on purpose: bound_message truncates
        # from the end, and a truncated password is a silently unusable digest.
        # <code> makes it tap-to-copy in Telegram.
        lines.append("CV password: <code>{}</code>".format(html.escape(password)))
    lines.append(" · ".join(bits))
    lines.append(page_url or "Open index.html in the archive.")
    return "\n".join(lines)


def publish_cvs(entries, date):
    """Encrypt and host one archive containing the tailored CVs.

    Returns ``(ok, password, zip_url)``. The page needs that URL, so this runs
    *before* publishing — which also means a failure leaves no page to retract.

    Nothing to upload is success, not failure: a review-only or deferred-only
    run has no CVs and must still get its page.

    Any encryption or upload exception takes the whole run back to the Telegram
    ZIP. The host is called only after the AES archive is complete, so raw PDF
    bytes can never reach it.
    """
    entries = [entry for entry in entries if entry.pdf_bytes]
    if not entries:
        return True, "", ""

    password = new_password()
    host = X0Client()
    try:
        archive = build_encrypted_cv_zip(entries, password)
        zip_url = host.upload(cv_archive_filename(date), archive)
    except Exception as exc:
        print(
            "  CV archive encryption/upload failed — falling back to the Telegram ZIP: {}".format(exc),
            file=sys.stderr,
        )
        return False, "", ""
    return True, password, zip_url


def deliver_telegram_digest(telegram, telegraph_token, ctx, rendered, date) -> DigestOutcome:
    """Send a whole run in ONE Telegram message, by one of two routes.

    With a telegra.ph token: host one encrypted archive of ordinary CV PDFs,
    publish a page linking to it, and send one message with the page URL and the
    archive password. Without it — or if encryption, upload or publishing fails —
    send the ZIP (``rendered`` as index.html plus the CVs) as one document, which
    is the delivery this pipeline has always done.

    Either way it is a single send, and the two routes are mutually exclusive by
    construction: the uploads happen before the page exists, so a failure leaves
    nothing published and nothing to retract.

    ``rendered`` is the caller's already-rendered dashboard HTML. Only the
    telegra.ph route consumes ``cv_zip_url``/``cv_encrypted`` (see
    ``digest.telegraph``), so setting them on ``ctx`` after ``rendered`` was
    produced loses nothing from the ZIP's index.html.
    """
    entries = list(ctx.fits) + list(ctx.review)
    use_telegraph = bool(telegraph_token)

    # Host the CVs before anything is published: the page carries links, so it
    # cannot be built until the archive URL exists — and a failure here means no
    # page was ever created, so there is nothing to retract. Gated on the token
    # because without one the run sends the ZIP and the file host is irrelevant.
    uploads_ok, cv_password = True, ""
    if use_telegraph:
        uploads_ok, cv_password, cv_zip_url = publish_cvs(entries, date)
        ctx.cv_zip_url = cv_zip_url
        ctx.cv_encrypted = bool(cv_password)

    # Telegraph first: a page plus a link message is the whole digest. Any
    # failure here (API down, content rejected, or an archive that could not be
    # encrypted/hosted) falls through to the ZIP.
    page_url = ""
    if use_telegraph and uploads_ok:
        try:
            page_url = publish_digest(TelegraphClient(), telegraph_token, ctx, date)
        except Exception as exc:
            print(
                f"  Telegraph publish failed — falling back to the ZIP: {exc}",
                file=sys.stderr,
            )

    caption = digest_caption(
        len(ctx.fits), len(ctx.review), len(ctx.deferred), date,
        page_url, cv_password,
    )
    zip_bytes = b""
    if not page_url:
        # Deliberately unguarded: a bundler that raises is a bug, not a delivery
        # problem, and turning it into a per-job delivery failure would burn the
        # retry ladder on something no retry can fix.
        zip_bytes = build_digest_zip(ctx, rendered)
        if len(zip_bytes) > TELEGRAM_DOC_LIMIT:
            # Diagnosable rather than silent: the send below will fail and the
            # fits will retry, but at least the log says why.
            print(
                f"  Digest is {len(zip_bytes) // (1024 * 1024)} MB, over Telegram's "
                f"{TELEGRAM_DOC_LIMIT // (1024 * 1024)} MB limit — send will likely fail.",
                file=sys.stderr,
            )

    try:
        if page_url:
            telegram.send_message(caption)
        else:
            telegram.send_document(digest_filename(date), zip_bytes, caption)
    except Exception as exc:
        if page_url:
            # The page went up but its link never reached the user, and the fits
            # are about to be queued for another run — which publishes another
            # page. Withdraw this one so the orphans do not pile up in the
            # index. Best-effort; never raises.
            retract_digest(TelegraphClient(), telegraph_token, page_url)
        return DigestOutcome(False, error=exc)

    # Every CV is delivered by now, whichever route the digest took: the ZIP
    # carries them itself, and a published page links the archive that was
    # hosted before the page existed. There is no per-CV delivery step.
    return DigestOutcome(
        True,
        notification_sent=True,
        cv_sent=sum(1 for entry in entries if entry.pdf_bytes),
    )


__all__ = [
    "TELEGRAM_DOC_LIMIT", "cv_archive_filename", "deliver_telegram_digest",
    "digest_caption", "publish_cvs",
]
