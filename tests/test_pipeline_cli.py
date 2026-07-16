"""Regressions for the shared description gate in manual tailoring."""
from types import SimpleNamespace

import pytest

from job_search.models import Job
from job_search.pipeline import cli, stages


def make_config():
    return SimpleNamespace(
        gemini_api_key="gemini",
        qwen_api_key="qwen",
        gemini_model="gemini-custom",
        gemini_api_base="https://gemini.example/models",
        qwen_model="qwen-custom",
        qwen_api_base="https://qwen.example/v1",
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

    def make_llm(*args, **kwargs):
        if llm_calls is not None:
            llm_calls.append((args, kwargs))
        return client

    monkeypatch.setattr(cli, "LLMClient", make_llm)
    monkeypatch.setattr(cli, "TelegramClient", lambda *_args: telegram)
    return client, telegram


def test_manual_tailor_forwards_provider_configuration(monkeypatch):
    llm_calls = []
    install_clients(monkeypatch, llm_calls)
    monkeypatch.setattr(cli, "tailor_single_job", lambda *_args: None)

    cli.run_tailor(make_args("x" * 200), make_config())

    assert llm_calls == [(('gemini', 'qwen'), {
        "gemini_model": "gemini-custom",
        "gemini_api_base": "https://gemini.example/models",
        "qwen_model": "qwen-custom",
        "qwen_api_base": "https://qwen.example/v1",
    })]


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
