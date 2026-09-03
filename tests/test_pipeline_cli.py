"""Regressions for the shared description gate in manual tailoring."""
from types import SimpleNamespace

import pytest

from job_search.models import Job
from job_search.pipeline import cli, stages


class RecordingTextBackend:
    """A text-only sink: no markup, no CV, no credentials.

    Stands in for whatever message-oriented adapter a user writes; the point of
    the tests using it is that the *renderer* decides the markup.
    """

    accepted_renderer_kinds = ("plain",)
    accepted_media_types = ()
    cv_mode = "disabled"
    requires_telegram_credentials = False

    def __init__(self, sink):
        self.sink = sink

    def deliver_notice(self, rendered):
        self.sink(str(rendered))

    def deliver_fit(self, rendered, artifact=None, notification_already_sent=False, *, job=None):
        from job_search.components import DeliveryOutcome

        self.sink(str(rendered))
        return DeliveryOutcome(
            notification_sent=True, notification_satisfied=True, cv_required=False
        )

    def deliver_digest(self, rendered, artifacts=(), *, context=None, date=None):
        from job_search.components import DigestOutcome

        self.sink(str(rendered))
        return DigestOutcome(True, notification_sent=True)


def make_config():
    return SimpleNamespace(
        llm_primary_scheme="gemini",
        llm_primary_model="gemini-custom",
        llm_primary_api_key="primary-key",
        llm_primary_api_base="https://gemini.example/models",
        llm_fallback_scheme="openai",
        llm_fallback_model="gpt-custom",
        llm_fallback_api_key="fallback-key",
        llm_fallback_api_base="https://oai.example/v1",
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )


def make_args(job_text, url="https://example.com/job"):
    return SimpleNamespace(
        job_text=job_text,
        url=url,
        title="iOS Engineer",
        company="Acme",
        location="Berlin",
    )


def install_components(monkeypatch, settings_seen=None, cv_mode="required"):
    """Install recording components; return (components, calls).

    run_tailor has one path now, so the components ARE the seam: what the CV
    renderer is handed, what the fit renderer produces, what the backend is
    asked to deliver.
    """
    from job_search.components import CVArtifact
    from job_search.pipeline.stages import DeliveryOutcome

    llm = object()
    artifact = CVArtifact("candidate.pdf", "application/pdf", b"PDF")
    calls = []

    class Renderer:
        def render_tailored(self, client, job, evaluation=None):
            calls.append(("cv", client, job))
            return artifact

    class OutputRenderer:
        kind = "plain"

        def render_fit(self, job, evaluation):
            calls.append(("render", job, evaluation["reason"]))
            return "rendered fit"

        def render_notice(self, notice, **_context):
            return str(notice)

    class Backend:
        cv_mode = "required"

        def deliver_fit(self, rendered, delivered_artifact=None,
                        notification_already_sent=False, *, job=None):
            calls.append(("deliver", rendered, delivered_artifact, job))
            return DeliveryOutcome(
                notification_sent=True, notification_satisfied=True, cv_sent=True
            )

        def deliver_notice(self, rendered):
            calls.append(("notice", rendered))

    Backend.cv_mode = cv_mode
    components = SimpleNamespace(
        llm=llm,
        cv_renderer=Renderer(),
        renderer=OutputRenderer(),
        backend=Backend(),
    )

    def _load(settings, command="daily", **_kwargs):
        if settings_seen is not None:
            settings_seen.append((settings, command))
        return components

    monkeypatch.setattr(cli, "build_runtime", _load)
    return components, calls, artifact


def test_manual_tailor_forwards_provider_configuration(monkeypatch):
    seen = []
    cfg = make_config()
    _components, calls, _artifact = install_components(monkeypatch, seen)

    cli.run_tailor(make_args("x" * 200), cfg)

    # Composition is handed the whole config, unchanged, for the tailor command.
    assert seen == [(cfg, "tailor")]
    assert seen[0][0].llm_primary_model == "gemini-custom"
    assert seen[0][0].llm_fallback_scheme == "openai"
    assert [call[0] for call in calls] == ["cv", "render", "deliver"]


def test_short_pasted_description_is_enriched_and_cleaned(monkeypatch):
    components, calls, _artifact = install_components(monkeypatch)
    monkeypatch.setattr(
        stages,
        "fetch_job_text_from_url",
        lambda _url: "<main>{}</main>".format("Complete iOS requirements &amp; details " * 10),
    )

    cli.run_tailor(make_args("x" * 20), make_config())

    _kind, client, job = calls[0]
    assert client is components.llm
    assert isinstance(job, Job)
    assert len(job.description) >= 200
    assert "<main>" not in job.description
    assert "&amp;" not in job.description


def test_sufficient_pasted_description_does_not_fetch(monkeypatch):
    _components, calls, _artifact = install_components(monkeypatch)
    monkeypatch.setattr(
        stages,
        "fetch_job_text_from_url",
        lambda _url: (_ for _ in ()).throw(AssertionError("no fetch")),
    )

    cli.run_tailor(make_args("x" * 200), make_config())

    _kind, _client, job = calls[0]
    assert isinstance(job, Job)
    assert job.description == "x" * 200


def test_unresolved_manual_job_exits_before_any_rendering_or_delivery(monkeypatch):
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "still short")
    _components, calls, _artifact = install_components(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        cli.run_tailor(make_args("x" * 20), make_config())

    assert exc_info.value.code == 1
    assert calls == []


def test_check_config_prints_redacted_configuration_without_dispatch(monkeypatch, capsys):
    cfg = make_config()
    components = object()
    monkeypatch.setattr(cli.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(cli, "build_runtime", lambda settings, command: components)
    monkeypatch.setattr(
        cli, "redacted_settings", lambda settings, loaded: '{"safe": true}'
    )
    monkeypatch.setattr(
        cli, "run_daily", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no daily"))
    )
    monkeypatch.setattr(cli.sys, "argv", ["job-search", "--check-config"])

    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == '{"safe": true}'


def test_manual_tailor_uses_custom_non_telegram_output_pair(monkeypatch):
    from job_search.components import CVArtifact
    from job_search.pipeline.stages import DeliveryOutcome

    cfg = make_config()
    cfg.telegram_bot_token = ""
    cfg.telegram_chat_id = ""
    artifact = CVArtifact("candidate.pdf", "application/pdf", b"PDF")
    calls = []

    class Renderer:
        def render_tailored(self, llm, job, evaluation=None):
            calls.append(("cv", llm, job.title))
            return artifact

    class OutputRenderer:
        def render_fit(self, job, evaluation):
            calls.append(("render", job.title, evaluation["reason"]))
            return "rendered fit"

    class Backend:
        cv_mode = "required"

        def deliver_fit(self, rendered, delivered_artifact=None,
                        notification_already_sent=False, *, job=None):
            calls.append(("deliver", rendered, delivered_artifact))
            return DeliveryOutcome(
                notification_sent=True,
                notification_satisfied=True,
                cv_sent=True,
            )

    llm = object()
    components = SimpleNamespace(
        llm=llm,
        cv_renderer=Renderer(),
        renderer=OutputRenderer(),
        backend=Backend(),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda *_a, **_k: components)

    cli.run_tailor(make_args("x" * 200), cfg)

    assert calls == [
        ("cv", llm, "iOS Engineer"),
        ("render", "iOS Engineer", cli.MANUAL_TAILOR_REASON),
        ("deliver", "rendered fit", artifact),
    ]


def test_manual_tailor_threads_custom_renderer_through_the_default_backend(monkeypatch):
    """A custom CV renderer and fit renderer reach Telegram through the one path.

    This used to be two tests, because a customized graph and an env-only file
    override took different code paths. There is one path now, so the assertion
    is simply that the configured objects are what produce the message and the
    document.
    """
    from job_search.components import CVArtifact, DefaultOutputBackend

    cfg = make_config()
    cfg.base_tex_file = "custom-base.tex"
    cfg.cv_tailoring_prompt_file = "custom-tailoring.md"
    artifact = CVArtifact("custom.pdf", "application/pdf", b"CUSTOM-PDF")
    observed = []

    class Telegram:
        def __init__(self):
            self.messages = []
            self.documents = []

        def send_message(self, message):
            self.messages.append(message)

        def send_document(self, filename, content, caption):
            self.documents.append((filename, content, caption))

    class Renderer:
        def render_tailored(self, client, job, evaluation=None):
            observed.append((client, job.title))
            return artifact

    class FitRenderer:
        kind = "telegram"

        def render_fit(self, _job, _evaluation):
            return "CUSTOM FIT"

    telegram = Telegram()
    llm = object()
    components = SimpleNamespace(
        llm=llm,
        cv_renderer=Renderer(),
        renderer=FitRenderer(),
        backend=DefaultOutputBackend(telegram),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda *_a, **_k: components)

    cli.run_tailor(make_args("x" * 200), cfg)

    assert observed == [(llm, "iOS Engineer")]
    assert telegram.messages == ["CUSTOM FIT"]
    assert telegram.documents == [
        ("custom.pdf", b"CUSTOM-PDF", "Tailored CV — iOS Engineer at Acme")
    ]


def test_manual_tailor_message_names_the_job_and_why_without_a_url(monkeypatch):
    """The manual tailor is rendered by the same fit renderer as any other fit.

    It used to have its own bespoke header ending in "📄 Tailored CV attached.";
    that text now rides in the reason line so there is one fit presentation
    rather than two.
    """
    from job_search.components import (
        CVArtifact,
        DefaultOutputBackend,
        DefaultOutputRenderer,
    )

    cfg = make_config()
    cfg.base_tex_file = "custom-base.tex"
    cfg.cv_tailoring_prompt_file = "custom-tailoring.md"

    class Telegram:
        def __init__(self):
            self.messages = []
            self.documents = []

        def send_message(self, message):
            self.messages.append(message)

        def send_document(self, filename, content, caption):
            self.documents.append((filename, content, caption))

    class Renderer:
        def render_tailored(self, _llm, _job, evaluation=None):
            return CVArtifact("candidate.pdf", "application/pdf", b"PDF")

    telegram = Telegram()
    components = SimpleNamespace(
        llm=object(),
        cv_renderer=Renderer(),
        renderer=DefaultOutputRenderer(),
        backend=DefaultOutputBackend(telegram),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda *_a, **_k: components)

    cli.run_tailor(make_args("x" * 200, url=""), cfg)

    assert telegram.messages == [
        "<b>iOS Engineer</b>\n<b>Acme</b> — Berlin\n\n"
        "<i>Manually requested — tailored CV attached.</i>"
    ]
    assert len(telegram.documents) == 1


def test_check_config_reports_unknown_provider_without_traceback(monkeypatch, capsys):
    from job_search.config import PipelineConfig

    cfg = PipelineConfig(llm_primary_scheme="typo")
    monkeypatch.setattr(cli.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(cli.sys, "argv", ["job-search", "--check-config"])

    assert cli.main() == 2
    captured = capsys.readouterr()
    assert "Unknown LLM scheme" in captured.err
    assert "Traceback" not in captured.err


def test_manual_tailor_renders_fatal_notice_with_composed_default_backend(monkeypatch):
    from job_search.components import DefaultOutputBackend

    notices = []

    class Telegram:
        def send_message(self, rendered):
            notices.append(("delivered", rendered))

    class Renderer:
        def render_notice(self, notice, **_context):
            notices.append(("rendered", notice))
            return "CUSTOM ERROR"

    components = SimpleNamespace(
        llm=object(),
        cv_renderer=SimpleNamespace(
            render_tailored=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("compile failed")
            )
        ),
        renderer=Renderer(),
        backend=DefaultOutputBackend(Telegram()),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda *_a, **_k: components)

    with pytest.raises(RuntimeError, match="compile failed"):
        cli.run_tailor(make_args("x" * 200), make_config())

    assert notices[0][0] == "rendered"
    assert notices[1] == ("delivered", "CUSTOM ERROR")


def test_manual_tailor_plain_backend_error_has_no_telegram_markup(monkeypatch):
    from job_search.output import PlainTextOutputRenderer

    messages = []
    components = SimpleNamespace(
        llm=object(),
        cv_renderer=SimpleNamespace(
            render_tailored=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("compile failed")
            )
        ),
        renderer=PlainTextOutputRenderer(),
        backend=RecordingTextBackend(messages.append),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda *_a, **_k: components)

    with pytest.raises(RuntimeError, match="compile failed"):
        cli.run_tailor(make_args("x" * 200), make_config())

    assert messages == ["Pipeline error: RuntimeError: compile failed"]
    assert "<" not in messages[0]
