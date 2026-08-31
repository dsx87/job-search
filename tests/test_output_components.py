import datetime
from types import SimpleNamespace

import job_search.output as output_module
from job_search.components import CVArtifact, DefaultOutputBackend
from job_search.digest.fixtures import sample_context
from job_search.output import (
    FilesystemOutputBackend,
    HtmlOutputRenderer,
    PlainTextOutputRenderer,
)
from job_search.models import Job


def test_filesystem_backend_writes_rendered_digest_and_generic_artifacts(tmp_path):
    renderer = HtmlOutputRenderer()
    backend = FilesystemOutputBackend(tmp_path, cv_mode="required")
    context = sample_context()
    artifact = CVArtifact("candidate.txt", "text/plain", b"candidate data")

    outcome = backend.deliver_digest(
        renderer.render_digest(context), [artifact], context=context
    )

    assert outcome.delivered is True
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (tmp_path / "cvs" / "candidate.txt").read_bytes() == b"candidate data"


def test_filesystem_digest_failure_keeps_previous_generation(monkeypatch, tmp_path):
    backend = FilesystemOutputBackend(tmp_path, cv_mode="required")
    old_artifacts = [
        CVArtifact("one.txt", "text/plain", b"old one"),
        CVArtifact("two.txt", "text/plain", b"old two"),
    ]
    assert backend.deliver_digest("old index", old_artifacts).delivered is True

    real_write = output_module._atomic_write

    def fail_second_artifact(path, content):
        if path.endswith("two.txt"):
            raise OSError("disk full")
        return real_write(path, content)

    monkeypatch.setattr(output_module, "_atomic_write", fail_second_artifact)
    outcome = backend.deliver_digest(
        "new index",
        [
            CVArtifact("one.txt", "text/plain", b"new one"),
            CVArtifact("two.txt", "text/plain", b"new two"),
        ],
    )

    assert outcome.delivered is False
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "old index"
    assert (tmp_path / "cvs" / "one.txt").read_bytes() == b"old one"
    assert (tmp_path / "cvs" / "two.txt").read_bytes() == b"old two"


def test_inherited_default_digest_backend_never_claims_unsent_cv_artifacts():
    messages = []

    class Telegram:
        def send_message(self, message):
            messages.append(message)

    class NoticeOnlyBackend(DefaultOutputBackend):
        pass

    artifact = CVArtifact("candidate.pdf", "application/pdf", b"PDF")
    outcome = NoticeOnlyBackend(Telegram()).deliver_digest(
        "rendered digest", [artifact]
    )

    assert outcome.delivered is True
    assert outcome.notification_sent is True
    assert outcome.cv_sent == 0
    assert messages == ["rendered digest"]
