"""Regressions for the shared description gate in manual tailoring."""
from types import SimpleNamespace

import pytest

from job_search.models import Job
from job_search.pipeline import cli, stages


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


def install_clients(monkeypatch, llm_calls=None):
    client = object()
    telegram = object()

    class FakeLLMClient:
        @staticmethod
        def from_config(cfg):
            if llm_calls is not None:
                llm_calls.append(cfg)
            return client

    monkeypatch.setattr(cli, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(cli, "TelegramClient", lambda *_args: telegram)
    return client, telegram


def test_manual_tailor_forwards_provider_configuration(monkeypatch):
    llm_calls = []
    cfg = make_config()
    install_clients(monkeypatch, llm_calls)
    monkeypatch.setattr(cli, "tailor_single_job", lambda *_args: None)

    cli.run_tailor(make_args("x" * 200), cfg)

    # LLMClient.from_config is handed the whole config, unchanged.
    assert llm_calls == [cfg]
    assert llm_calls[0].llm_primary_model == "gemini-custom"
    assert llm_calls[0].llm_fallback_scheme == "openai"


def test_short_pasted_description_is_enriched_and_cleaned(monkeypatch):
    client, telegram = install_clients(monkeypatch)
    monkeypatch.setattr(
        stages,
        "fetch_job_text_from_url",
        lambda _url: "<main>{}</main>".format("Complete iOS requirements &amp; details " * 10),
    )
    received = []
    monkeypatch.setattr(cli, "tailor_single_job", lambda c, job, t: received.append((c, job, t)))

    cli.run_tailor(make_args("x" * 20), make_config())

    assert received[0][0] is client
    assert received[0][2] is telegram
    assert isinstance(received[0][1], Job)
    assert len(received[0][1].description) >= 200
    assert "<main>" not in received[0][1].description
    assert "&amp;" not in received[0][1].description


def test_sufficient_pasted_description_does_not_fetch(monkeypatch):
    install_clients(monkeypatch)
    monkeypatch.setattr(
        stages,
        "fetch_job_text_from_url",
        lambda _url: (_ for _ in ()).throw(AssertionError("no fetch")),
    )
    received = []
    monkeypatch.setattr(cli, "tailor_single_job", lambda _c, job, _t: received.append(job))

    cli.run_tailor(make_args("x" * 200), make_config())

    assert isinstance(received[0], Job)
    assert received[0].description == "x" * 200


def test_unresolved_manual_job_exits_before_constructing_clients(monkeypatch):
    monkeypatch.setattr(stages, "fetch_job_text_from_url", lambda _url: "still short")
    monkeypatch.setattr(cli, "LLMClient", lambda *_args: (_ for _ in ()).throw(AssertionError("no LLM")))
    monkeypatch.setattr(cli, "TelegramClient", lambda *_args: (_ for _ in ()).throw(AssertionError("no Telegram")))

    with pytest.raises(SystemExit) as exc_info:
        cli.run_tailor(make_args("x" * 20), make_config())

    assert exc_info.value.code == 1


def test_check_config_prints_redacted_configuration_without_dispatch(monkeypatch, capsys):
    cfg = make_config()
    components = object()
    monkeypatch.setattr(cli.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(cli, "load_components", lambda settings, command: components)
    monkeypatch.setattr(
        cli, "redacted_configuration", lambda settings, loaded: '{"safe": true}'
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

        def deliver_fit(self, rendered, delivered_artifact, notification_already_sent=False):
            calls.append(("deliver", rendered, delivered_artifact))
            return DeliveryOutcome(
                notification_sent=True,
                notification_satisfied=True,
                cv_sent=True,
            )

    llm = object()
    components = SimpleNamespace(
        _customized=True,
        llm=llm,
        cv_renderer=Renderer(),
        output_renderer=OutputRenderer(),
        output_backend=Backend(),
    )
    monkeypatch.setattr(cli, "load_components", lambda *_a, **_k: components)
    monkeypatch.setattr(
        cli, "TelegramClient",
        lambda *_a: (_ for _ in ()).throw(AssertionError("no Telegram client")),
    )
    monkeypatch.setattr(
        cli, "tailor_single_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no legacy delivery")),
    )

    cli.run_tailor(make_args("x" * 200), cfg)

    assert calls == [
        ("cv", llm, "iOS Engineer"),
        ("render", "iOS Engineer", "Manual tailoring"),
        ("deliver", "rendered fit", artifact),
    ]
