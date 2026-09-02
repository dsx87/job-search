"""Reusable pure renderers and non-Telegram output backends."""
from __future__ import annotations

import html
import os
import shutil
import tempfile
import threading

from .components import DeliveryOutcome, DigestOutcome
from .digest.render import render_digest_html
from .models import coerce_job


class HtmlOutputRenderer:
    """Pure HTML presentation suitable for files, sites, or custom adapters."""

    def render_notice(self, notice, **context):
        if context.get("level") == "error":
            return "<p><strong>{}:</strong> {}</p>".format(
                html.escape(str(context.get("title", "Pipeline error"))),
                html.escape(str(notice)),
            )
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

    def render_notice(self, notice, **context):
        if context.get("level") == "error":
            return "{}: {}".format(
                context.get("title", "Pipeline error"), notice
            )
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
    """Publish renderer output and artifacts as one filesystem generation."""

    def __init__(self, directory, require_artifact=True):
        self.directory = os.path.abspath(os.fspath(directory))
        self.require_artifact = bool(require_artifact)
        self._lock = threading.Lock()

    @property
    def _releases_directory(self):
        return os.path.join(self.directory, ".job-search-releases")

    @property
    def _current_link(self):
        return os.path.join(self.directory, ".job-search-current")

    @staticmethod
    def _temporary_symlink(directory, target):
        fd, temporary = tempfile.mkstemp(
            dir=directory, prefix=".job-search-link-"
        )
        os.close(fd)
        os.unlink(temporary)
        try:
            os.symlink(target, temporary)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return temporary

    def _replace_symlink(self, path, target):
        temporary = self._temporary_symlink(self.directory, target)
        try:
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _install_public_link(self, name):
        path = os.path.join(self.directory, name)
        target = os.path.join(".job-search-current", name)
        if os.path.islink(path) and os.readlink(path) == target:
            return

        temporary = self._temporary_symlink(self.directory, target)
        backup = None
        try:
            # os.replace cannot replace a nonempty directory with a symlink.
            # Bootstrap copies it into the first release before this rename,
            # so moving it aside changes no published content.
            if os.path.isdir(path) and not os.path.islink(path):
                backup = tempfile.mkdtemp(
                    dir=self.directory, prefix=".job-search-legacy-"
                )
                os.rmdir(backup)
                os.replace(path, backup)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            if backup is not None and not os.path.lexists(path):
                os.replace(backup, path)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)

    def _ensure_generation_layout(self):
        os.makedirs(self.directory, exist_ok=True)
        os.makedirs(self._releases_directory, exist_ok=True)

        if not os.path.islink(self._current_link):
            if os.path.lexists(self._current_link):
                raise ValueError("filesystem output current pointer is not a symlink")
            bootstrap = tempfile.mkdtemp(
                dir=self._releases_directory, prefix="generation-"
            )
            try:
                for name in ("notice.txt", "latest-fit.html", "index.html", "cvs"):
                    source = os.path.join(self.directory, name)
                    destination = os.path.join(bootstrap, name)
                    if os.path.isdir(source):
                        shutil.copytree(source, destination)
                    elif os.path.isfile(source):
                        shutil.copy2(source, destination)
                os.makedirs(os.path.join(bootstrap, "cvs"), exist_ok=True)
                self._replace_symlink(
                    self._current_link,
                    os.path.relpath(bootstrap, self.directory),
                )
            except BaseException:
                shutil.rmtree(bootstrap, ignore_errors=True)
                raise

        # The stable public paths all traverse one pointer. Replacing that
        # pointer commits every file in a later generation simultaneously.
        for name in ("cvs", "notice.txt", "latest-fit.html", "index.html"):
            self._install_public_link(name)

        current = os.path.realpath(self._current_link)
        releases = os.path.realpath(self._releases_directory)
        try:
            contained = os.path.commonpath((current, releases)) == releases
        except ValueError:
            contained = False
        if not contained or not os.path.isdir(current):
            raise ValueError("filesystem output current pointer is invalid")
        return current

    def _publish(self, writes):
        with self._lock:
            current = self._ensure_generation_layout()
            generation = tempfile.mkdtemp(
                dir=self._releases_directory, prefix="generation-"
            )
            committed = False
            try:
                shutil.copytree(current, generation, dirs_exist_ok=True)
                for relative_path, content in writes:
                    _atomic_write(
                        os.path.join(generation, relative_path), content
                    )
                self._replace_symlink(
                    self._current_link,
                    os.path.relpath(generation, self.directory),
                )
                committed = True
            finally:
                if not committed:
                    shutil.rmtree(generation, ignore_errors=True)
            if current != generation:
                shutil.rmtree(current, ignore_errors=True)

    def deliver_notice(self, rendered):
        content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
        self._publish((("notice.txt", content),))

    def deliver_fit(self, rendered, artifact=None, notification_already_sent=False, *, job=None):
        try:
            if self.require_artifact and artifact is None:
                raise ValueError("a CV artifact is required for filesystem delivery")
            content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
            writes = [("latest-fit.html", content)]
            if artifact is not None:
                writes.append(
                    (
                        os.path.join("cvs", _safe_filename(artifact.filename)),
                        artifact.content,
                    )
                )
            self._publish(writes)
        except Exception as exc:
            return DeliveryOutcome(error=exc, cv_required=self.require_artifact)
        return DeliveryOutcome(
            notification_sent=not notification_already_sent,
            notification_satisfied=True,
            cv_sent=artifact is not None,
            cv_required=self.require_artifact,
        )

    def deliver_digest(self, rendered, artifacts=(), *, context=None, date=None):
        try:
            artifacts = tuple(artifacts)
            content = rendered if isinstance(rendered, bytes) else str(rendered).encode("utf-8")
            writes = []
            for artifact in artifacts:
                writes.append(
                    (
                        os.path.join("cvs", _safe_filename(artifact.filename)),
                        artifact.content,
                    )
                )
            writes.append(("index.html", content))
            self._publish(writes)
        except Exception as exc:
            return DigestOutcome(False, error=exc)
        return DigestOutcome(
            True, notification_sent=True, cv_sent=len(artifacts)
        )


__all__ = [
    "FilesystemOutputBackend", "HtmlOutputRenderer", "PlainTextOutputRenderer",
]
