"""Pure command parsing + presentation — zero I/O, the main test surface.

Everything here is a total function of its inputs (no network, subprocess,
clock, or filesystem), so the bot's decision logic can be exercised offline.
The stateful edges (HTTP, Popen, files) live in runner.py / poller.py.
"""
import datetime
import html
from collections import namedtuple

Command = namedtuple("Command", ["verb", "arg"])


def parse_command(text):
    """Parse a Telegram message into a Command, or None if it isn't a command.

    Strips a trailing ``@botname`` off the verb and lowercases the verb; the
    argument keeps its original case and internal whitespace (a pasted job
    description or a case-sensitive URL must survive intact). Non-slash text and
    blank messages return None.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    # First whitespace run splits the verb from the arg; split(None, 1) drops
    # the arg's leading whitespace but preserves its internal whitespace.
    parts = stripped.split(None, 1)
    word = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    verb = word[1:].split("@", 1)[0].lower()
    return Command(verb, arg)


def tailor_argv(arg):
    """Map a /tailor argument to the pipeline's --url / --job-text flag.

    A URL (``http…``) is auto-fetched by the CLI; anything else is treated as a
    pasted job description.
    """
    if arg.startswith("http"):
        return ["--url", arg]
    return ["--job-text", arg]


def is_stale(msg_date, now, max_age=600):
    """True if a message is older than max_age seconds.

    msg_date is Telegram's UTC epoch ``message.date``; now is ``time.time()``
    (also UTC), so the comparison is timezone-agnostic. Guards against a /run
    queued during an outage firing on reboot.
    """
    return (now - msg_date) > max_age


def stderr_tail(text, max_lines=15, max_chars=1500):
    """The last few non-blank lines of stderr, for relaying to Telegram."""
    if not text:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail.strip()


def format_duration(seconds):
    """Human-readable duration: ``45s`` / ``1m 30s`` / ``1h 1m``."""
    seconds = int(round(seconds))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return "{}m {}s".format(minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "{}h {}m".format(hours, minutes)


def iso_utc(epoch):
    """Epoch seconds → ``YYYY-MM-DD HH:MM UTC`` (for /status headers)."""
    when = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    return when.strftime("%Y-%m-%d %H:%M UTC")


def render_status(last_run, in_progress, uptime, now_iso):
    """Render the /status reply for the present / absent / in-progress cases.

    last_run is the parsed .last_run.json dict (or None if never run); the
    caller decides in_progress (an unfinished .last_run.json or a live runner).
    """
    lines = ["<b>Job-search bot</b> — {}".format(now_iso)]
    if in_progress:
        if last_run:
            lines.append(
                "🟢 A run is in progress (trigger: {}, started {}).".format(
                    last_run.get("trigger", "?"), last_run.get("started", "?")
                )
            )
        else:
            lines.append("🟢 A run is in progress.")
    elif last_run is None:
        lines.append("⚪️ Idle — no pipeline runs recorded yet.")
    else:
        rc = last_run.get("exit_code")
        icon = "✅" if rc == 0 else "❌"
        lines.append(
            "⚪️ Idle. Last run {} (trigger: {}, exit {}).".format(
                icon, last_run.get("trigger", "?"), rc
            )
        )
        lines.append(
            "    started {}, finished {}".format(
                last_run.get("started", "?"), last_run.get("finished", "?")
            )
        )
    lines.append("Uptime: {}".format(uptime))
    return "\n".join(lines)


def escape(text):
    """HTML-escape untrusted text before it goes into an HTML-parse-mode message."""
    return html.escape(text)
