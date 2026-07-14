"""Long-poll Telegram, authorize, and dispatch commands.

The transport (``http_request``), the update source, the runner, the send
function, the clock, and sleep are all injected so the poll loop is testable
offline (``tests/test_bot_poller.py`` feeds canned updates and asserts on a list
of sends — no real network, subprocess, thread, or sleep).
"""
import json
import os

from ..http import http_request
from .commands import (
    format_duration,
    is_stale,
    iso_utc,
    parse_command,
    render_status,
    tailor_argv,
)

# Seconds to wait between failed poll attempts (network errors → sleep + retry).
RETRY_SLEEP_SECONDS = 5


def telegram_get_updates(token, offset, timeout, transport=http_request):
    """One ``getUpdates`` long-poll; returns the list of update objects.

    Uses ``http_request`` (not ``http_json``) for its timeout passthrough — the
    socket timeout must exceed the long-poll ``timeout`` or the poll would abort
    before Telegram replies.
    """
    url = "https://api.telegram.org/bot{}/getUpdates".format(token)
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    _status, text = transport(url, params=params, timeout=timeout + 10)
    data = json.loads(text)
    if not data.get("ok", False):
        raise RuntimeError("getUpdates returned not-ok: {}".format(data))
    return data.get("result", [])


def set_my_commands(token, transport=http_request):
    """Register the /run, /status, /tailor autocomplete menu with Telegram.

    Best-effort: the menu is cosmetic and the commands work without it, so a
    boot-time network hiccup must not stop the bot from starting.
    """
    url = "https://api.telegram.org/bot{}/setMyCommands".format(token)
    payload = {
        "commands": [
            {"command": "run", "description": "Run the full job-search pipeline now"},
            {"command": "status", "description": "Show the last/current run and bot uptime"},
            {"command": "tailor", "description": "Tailor a CV against a job URL or pasted description"},
        ]
    }
    try:
        transport(url, method="POST", json_body=payload, timeout=30)
    except Exception:
        pass  # cosmetic; commands work regardless


class OffsetStore:
    """Persist the getUpdates offset so a bot restart doesn't replay commands."""

    def __init__(self, path):
        self.path = path

    def load(self):
        """Return the stored offset, or None if missing/corrupt."""
        try:
            with open(self.path) as handle:
                return int(json.load(handle)["offset"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, offset):
        tmp = "{}.tmp".format(self.path)
        with open(tmp, "w") as handle:
            json.dump({"offset": offset}, handle)
        os.replace(tmp, self.path)


def read_last_run(path):
    """Parse .last_run.json tolerantly; return None on missing/garbage.

    /status must never crash on a truncated or malformed run record.
    """
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Poller:
    """The poll loop: authorize → staleness-gate → parse → dispatch."""

    def __init__(
        self,
        chat_id,
        runner,
        send,
        offset_store,
        last_run_path,
        get_updates,
        clock,
        sleep,
        stale_after=600,
    ):
        self.chat_id = str(chat_id)
        self.runner = runner
        self.send = send
        self.offset_store = offset_store
        self.last_run_path = last_run_path
        self.get_updates = get_updates
        self.clock = clock
        self.sleep = sleep
        self.stale_after = stale_after
        self.offset = offset_store.load()
        self.started_at = clock()
        self._running = False

    # ---- dispatch ---------------------------------------------------------
    def handle_update(self, update):
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        # Auth first: anything not from the one authorized chat is ignored
        # silently (no reply — don't confirm the bot exists to strangers).
        chat = message.get("chat", {})
        if str(chat.get("id")) != self.chat_id:
            return
        # Staleness: an old message (e.g. a /run queued during an outage) is
        # acked via the advancing offset but must NOT fire on reboot.
        if is_stale(message.get("date", 0), self.clock(), self.stale_after):
            return
        command = parse_command(message.get("text", ""))
        if command is None:
            return
        if command.verb == "run":
            self._dispatch_run()
        elif command.verb == "status":
            self._dispatch_status()
        elif command.verb == "tailor":
            self._dispatch_tailor(command.arg)
        else:
            self.send("Unknown command. Try /run, /status, or /tailor <url|description>.")

    def _dispatch_run(self):
        if self.runner.start("run", [], self.send):
            self.send("🚀 Started a pipeline run — I'll message you when it finishes.")
        else:
            self.send("⏳ A run is already in progress.")

    def _dispatch_tailor(self, arg):
        arg = arg.strip()
        if not arg:
            self.send("Usage: /tailor <job-url>  —or—  /tailor <pasted job description>")
            return
        extra_args = ["--tailor"] + tailor_argv(arg)
        if self.runner.start("tailor", extra_args, self.send):
            self.send("🚀 Tailoring a CV — I'll send the PDF when it's ready.")
        else:
            self.send("⏳ A run is already in progress.")

    def _dispatch_status(self):
        last_run = read_last_run(self.last_run_path)
        now = self.clock()
        in_progress = self.runner.is_running() or (
            last_run is not None and last_run.get("finished") is None
        )
        uptime = format_duration(now - self.started_at)
        self.send(render_status(last_run, in_progress, uptime, iso_utc(now)))

    # ---- loop -------------------------------------------------------------
    def poll_once(self):
        updates = self.get_updates(self.offset)
        for update in updates:
            try:
                self.handle_update(update)
            except Exception as exc:  # one poison update must not wedge the loop
                print("[bot] error handling update {}: {}".format(update.get("update_id"), exc), flush=True)
            self.offset = update["update_id"] + 1
            self.offset_store.save(self.offset)

    def run(self):
        self._running = True
        while self._running:
            try:
                self.poll_once()
            except Exception as exc:
                print("[bot] poll error: {} — retrying in {}s".format(exc, RETRY_SLEEP_SECONDS), flush=True)
                self.sleep(RETRY_SLEEP_SECONDS)

    def stop(self):
        self._running = False
