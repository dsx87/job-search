import datetime
from types import SimpleNamespace

from job_search.components import CVArtifact
from job_search.digest.fixtures import sample_context
from job_search.output import (
    FilesystemOutputBackend,
    HtmlOutputRenderer,
    PlainMessageBackend,
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


def test_plain_message_backend_reports_text_only_fit_completion():
    messages = []
    renderer = PlainTextOutputRenderer()
    backend = PlainMessageBackend(messages.append)
    job = Job(title="iOS Engineer", company="Acme", url="https://example.com")

    outcome = backend.deliver_fit(renderer.render_fit(job, {"reason": "A match"}))

    assert outcome.complete is True
    assert outcome.cv_sent is False
    assert messages and "iOS Engineer" in messages[0]


def test_plain_message_backend_delivers_digest_as_one_message():
    messages = []
    backend = PlainMessageBackend(messages.append)
    rendered = PlainTextOutputRenderer().render_digest(sample_context())

    outcome = backend.deliver_digest(rendered)

    assert outcome.delivered is True
    assert outcome.notification_sent is True
    assert messages == [rendered]
