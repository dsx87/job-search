"""Reusable pure renderers and non-Telegram output backends."""
from __future__ import annotations

import html
import os
import tempfile

from .components import CVArtifact, DigestOutcome
from .digest.render import render_digest_html
from .models import coerce_job


class HtmlOutputRenderer:
    """Pure HTML presentation suitable for files, sites, or custom adapters."""

    kind = "html"

    def render_notice(self, notice, **context):
        return "<p>{}</p>".format(html.escape(str(notice)))

    def render_fit(self, job, evaluation):
        job = coerce_job(job)
        reason = str((evaluation or {}).get("reason", ""))
        return (
            "<article><h2>{}</h2><p>{}</p><p>{}</p></article>".format(
                html.escape(job.title), html.escape(job.company), html.escape(reason)
            )
        )

    def render_digest(self, context):
        return render_digest_html(context)


class PlainTextOutputRenderer:
    """Pure portable text presentation for message-oriented adapters."""

    kind = "plain"

    def render_notice(self, notice, **context):
        return str(notice)

    def render_fit(self, job, evaluation):
        job = coerce_job(job)
        reason = str((evaluation or {}).get("reason", "")).strip()
        lines = [job.title or "Unknown role", job.company or "Unknown company"]
        if job.url:
            lines.append(job.url)
        if reason:
            lines.append(reason)
        return "\n".join(lines)

    def render_digest(self, context):
        lines = ["Job Search Digest — {}".format(context.date)]
        lines.append("{} fit(s), {} to review, {} deferred".format(
            len(context.fits), len(context.review), len(context.deferred)
        ))
        for entry in context.fits:
            job = coerce_job(entry.job)
            lines.append("FIT: {} — {}".format(job.title, job.company))
        for entry in context.review:
            job = coerce_job(entry.job)
            lines.append("REVIEW: {} — {}".format(job.title, job.company))
        return "\n".join(lines)


def _safe_filename(value):
    name = os.path.basename(str(value or "").strip())
    if not name or name in (".", ".."):
        raise ValueError("artifact filename must name a file")
    return name


def _atomic_write(path, content):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".job-search-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class FilesystemOutputBackend:
    """Write renderer output and artifacts to a local directory atomically."""

    accepted_renderer_kinds = ("html", "plain")
    accepted_media_types = (
        "application/pdf", "application/zip", "application/octet-stream",
        "text/plain", "text/html",
    )
    requires_telegram_credentials = False

    def __init__(self, directory, cv_mode="required"):
        if cv_mode not in ("required", "disabled"):
            raise ValueError("cv_mode must be 'required' or 'disabled'")
        self.directory = os.fspath(directory)
        self.cv_mode = cv_mode

    def deliver_notice(self, rendered):
        content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
        _atomic_write(os.path.join(self.directory, "notice.txt"), content)

    def deliver_fit(self, rendered, artifact=None, notification_already_sent=False):
        from .pipeline.stages import DeliveryOutcome

        try:
            content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
            _atomic_write(os.path.join(self.directory, "latest-fit.html"), content)
            if artifact is not None:
                _atomic_write(
                    os.path.join(self.directory, "cvs", _safe_filename(artifact.filename)),
                    artifact.content,
                )
        except Exception as exc:
            return DeliveryOutcome(error=exc, cv_required=self.cv_mode == "required")
        return DeliveryOutcome(
            notification_sent=not notification_already_sent,
            notification_satisfied=True,
            cv_sent=artifact is not None,
            cv_required=self.cv_mode == "required",
        )

    def deliver_digest(self, rendered, artifacts=(), **context):
        try:
            content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
            _atomic_write(os.path.join(self.directory, "index.html"), content)
            for artifact in artifacts:
                _atomic_write(
                    os.path.join(self.directory, "cvs", _safe_filename(artifact.filename)),
                    artifact.content,
                )
        except Exception as exc:
            return DigestOutcome(False, error=exc)
        return DigestOutcome(
            True, notification_sent=True, cv_sent=len(tuple(artifacts))
        )


class PlainMessageBackend:
    """Small adapter base for SMS/Slack/WhatsApp-like text send callables."""

    accepted_renderer_kinds = ("plain",)
    accepted_media_types = ()
    cv_mode = "disabled"
    requires_telegram_credentials = False

    def __init__(self, send):
        if not callable(send):
            raise TypeError("send must be callable")
        self.send = send

    def deliver_notice(self, rendered):
        return self.send(str(rendered))

    def deliver_fit(self, rendered, artifact=None, notification_already_sent=False):
        from .pipeline.stages import DeliveryOutcome

        if notification_already_sent:
            return DeliveryOutcome(
                notification_satisfied=True, cv_required=False
            )
        try:
            self.send(str(rendered))
        except Exception as exc:
            return DeliveryOutcome(error=exc, cv_required=False)
        return DeliveryOutcome(
            notification_sent=True,
            notification_satisfied=True,
            cv_required=False,
        )

    def deliver_digest(self, rendered, artifacts=(), **context):
        try:
            self.send(str(rendered))
        except Exception as exc:
            return DigestOutcome(False, error=exc)
        return DigestOutcome(True, notification_sent=True)


__all__ = [
    "FilesystemOutputBackend", "HtmlOutputRenderer", "PlainMessageBackend",
    "PlainTextOutputRenderer",
]
