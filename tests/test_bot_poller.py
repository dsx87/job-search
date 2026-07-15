"""Tests for the Poller with injected updates / runner / store (no real I/O)."""
import json

from job_search.bot.poller import (
    OffsetStore,
    Poller,
    read_last_run,
    set_my_commands,
    telegram_get_updates,
)


class FakeRunner:
    def __init__(self, start_result=True, running=False):
        self.start_result = start_result
        self.running = running
        self.starts = []

    def start(self, trigger, extra_args, reply):
        self.starts.append((trigger, list(extra_args)))
        return self.start_result

    def is_running(self):
        return self.running


def make_update(update_id, text, chat_id="42", date=1000):
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "date": date, "text": text},
    }


def make_poller(tmp_path, runner=None, updates=None, clock_value=1000.0, chat_id="42"):
    runner = runner if runner is not None else FakeRunner()
    sends = []
    store = OffsetStore(str(tmp_path / ".bot_offset"))
    canned = list(updates or [])

    def get_updates(_offset):
        batch, canned[:] = canned[:], []  # deliver once, then empty
        return batch

    poller = Poller(
        chat_id=chat_id,
        runner=runner,
        send=sends.append,
        offset_store=store,
        last_run_path=str(tmp_path / ".last_run.json"),
        get_updates=get_updates,
        clock=lambda: clock_value,
        sleep=lambda _s: None,
        stale_after=600,
    )
    return poller, sends, runner, store


# ---- auth -----------------------------------------------------------------
def test_auth_drop_foreign_chat(tmp_path):
    poller, sends, runner, store = make_poller(
        tmp_path, updates=[make_update(10, "/run", chat_id="999")]
    )
    poller.poll_once()
    assert runner.starts == []      # never started
    assert sends == []              # no reply to a stranger
    assert store.load() == 11       # offset still advances (acked)


# ---- /run -----------------------------------------------------------------
def test_run_dispatch(tmp_path):
    poller, sends, runner, store = make_poller(tmp_path, updates=[make_update(20, "/run")])
    poller.poll_once()
    assert runner.starts == [("run", [])]
    assert any("started" in s.lower() for s in sends)
    assert store.load() == 21


def test_run_busy_replies_in_progress(tmp_path):
    runner = FakeRunner(start_result=False)
    poller, sends, _runner, _store = make_poller(
        tmp_path, runner=runner, updates=[make_update(21, "/run")]
    )
    poller.poll_once()
    assert any("already in progress" in s.lower() for s in sends)


# ---- /status --------------------------------------------------------------
def test_status_absent(tmp_path):
    poller, sends, _runner, _store = make_poller(tmp_path, updates=[make_update(30, "/status")])
    poller.poll_once()
    assert len(sends) == 1
    assert "uptime" in sends[0].lower()


def test_status_present_success(tmp_path):
    (tmp_path / ".last_run.json").write_text(
        json.dumps({"trigger": "timer", "started": "s", "finished": "f", "exit_code": 0})
    )
    poller, sends, _runner, _store = make_poller(tmp_path, updates=[make_update(31, "/status")])
    poller.poll_once()
    assert "timer" in sends[0]
    assert "✅" in sends[0]


def test_status_in_progress_from_unfinished_last_run(tmp_path):
    (tmp_path / ".last_run.json").write_text(
        json.dumps({"trigger": "timer", "started": "s", "finished": None, "exit_code": None})
    )
    poller, sends, _runner, _store = make_poller(tmp_path, updates=[make_update(32, "/status")])
    poller.poll_once()
    assert "in progress" in sends[0].lower()


# ---- staleness ------------------------------------------------------------
def test_staleness_guard_acks_but_does_not_run(tmp_path):
    poller, sends, runner, store = make_poller(
        tmp_path, clock_value=100000.0, updates=[make_update(40, "/run", date=100)]
    )
    poller.poll_once()
    assert runner.starts == []     # not fired
    assert sends == []             # silently acked
    assert store.load() == 41      # offset advanced


# ---- offset ---------------------------------------------------------------
def test_offset_roundtrip(tmp_path):
    store = OffsetStore(str(tmp_path / ".bot_offset"))
    assert store.load() is None
    store.save(123)
    assert store.load() == 123


def test_offset_corrupt_returns_none(tmp_path):
    path = tmp_path / ".bot_offset"
    path.write_text("not json {{{")
    assert OffsetStore(str(path)).load() is None


def test_offset_persists_after_each_update(tmp_path):
    poller, _sends, _runner, store = make_poller(
        tmp_path, updates=[make_update(80, "/status"), make_update(81, "/status")]
    )
    poller.poll_once()
    assert store.load() == 82


# ---- misc dispatch --------------------------------------------------------
def test_unknown_command(tmp_path):
    poller, sends, runner, _store = make_poller(tmp_path, updates=[make_update(50, "/frobnicate")])
    poller.poll_once()
    assert runner.starts == []
    assert len(sends) == 1
    assert "unknown" in sends[0].lower()


def test_tailor_empty_arg_shows_usage(tmp_path):
    poller, sends, runner, _store = make_poller(tmp_path, updates=[make_update(60, "/tailor")])
    poller.poll_once()
    assert runner.starts == []
    assert "usage" in sends[0].lower()


def test_tailor_url_dispatch(tmp_path):
    poller, _sends, runner, _store = make_poller(
        tmp_path, updates=[make_update(61, "/tailor https://example.com/j")]
    )
    poller.poll_once()
    assert runner.starts == [("tailor", ["--tailor", "--url", "https://example.com/j"])]


def test_tailor_pasted_text_dispatch(tmp_path):
    poller, _sends, runner, _store = make_poller(
        tmp_path, updates=[make_update(62, "/tailor Senior iOS Engineer, Swift/SwiftUI")]
    )
    poller.poll_once()
    assert runner.starts == [
        ("tailor", ["--tailor", "--job-text", "Senior iOS Engineer, Swift/SwiftUI"])
    ]


# ---- read_last_run --------------------------------------------------------
def test_read_last_run_missing(tmp_path):
    assert read_last_run(str(tmp_path / "nope.json")) is None


def test_read_last_run_garbage(tmp_path):
    path = tmp_path / ".last_run.json"
    path.write_text("{not valid")
    assert read_last_run(str(path)) is None


# ---- set_my_commands ------------------------------------------------------
def test_set_my_commands_posts_expected_menu():
    calls = []

    def transport(url, method="GET", json_body=None, timeout=None, **_kw):
        calls.append({"url": url, "method": method, "json_body": json_body})
        return (200, '{"ok": true}')

    set_my_commands("TOKEN", transport=transport)

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "setMyCommands" in calls[0]["url"]
    verbs = [c["command"] for c in calls[0]["json_body"]["commands"]]
    assert verbs == ["run", "status", "tailor"]


def test_set_my_commands_swallows_transport_error():
    def transport(*_a, **_k):
        raise RuntimeError("network down")

    # Best-effort: must not raise.
    set_my_commands("TOKEN", transport=transport)


# ---- telegram_get_updates -------------------------------------------------
def test_telegram_get_updates_builds_request_and_parses():
    captured = {}

    def transport(url, params=None, timeout=None, **_kw):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return (200, json.dumps({"ok": True, "result": [{"update_id": 7}]}))

    result = telegram_get_updates("TOK", offset=5, timeout=50, transport=transport)

    assert result == [{"update_id": 7}]
    assert "getUpdates" in captured["url"]
    assert captured["params"] == {"timeout": 50, "offset": 5}
    assert captured["timeout"] == 60  # long-poll timeout + margin


def test_telegram_get_updates_omits_offset_when_none():
    def transport(url, params=None, timeout=None, **_kw):
        assert "offset" not in params
        return (200, json.dumps({"ok": True, "result": []}))

    assert telegram_get_updates("TOK", offset=None, timeout=50, transport=transport) == []
