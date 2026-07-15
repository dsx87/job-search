"""Pure-logic tests for the bot's command parsing + presentation (zero I/O)."""
from job_search.bot.commands import (
    Command,
    format_duration,
    is_stale,
    iso_utc,
    parse_command,
    render_status,
    stderr_tail,
    tailor_argv,
)


# ---- parse_command --------------------------------------------------------
def test_parse_run():
    assert parse_command("/run") == Command("run", "")


def test_parse_status():
    assert parse_command("/status") == Command("status", "")


def test_parse_tailor_url():
    assert parse_command("/tailor https://example.com/job/1") == Command(
        "tailor", "https://example.com/job/1"
    )


def test_parse_tailor_multiword_preserves_internal_whitespace():
    cmd = parse_command("/tailor Senior iOS  Engineer   remote")
    assert cmd.verb == "tailor"
    assert cmd.arg == "Senior iOS  Engineer   remote"


def test_parse_strips_botname():
    assert parse_command("/run@MyJobBot") == Command("run", "")
    assert parse_command("/tailor@MyJobBot https://x") == Command("tailor", "https://x")


def test_parse_lowercases_verb_but_not_arg():
    cmd = parse_command("/TAILOR https://Example.com/Job")
    assert cmd.verb == "tailor"
    assert cmd.arg == "https://Example.com/Job"


def test_parse_non_slash_returns_none():
    assert parse_command("hello there") is None


def test_parse_blank_returns_none():
    assert parse_command("") is None
    assert parse_command("   ") is None
    assert parse_command(None) is None


def test_parse_leading_and_trailing_whitespace():
    assert parse_command("  /run  ") == Command("run", "")


# ---- tailor_argv ----------------------------------------------------------
def test_tailor_argv_url():
    assert tailor_argv("https://example.com/j") == ["--url", "https://example.com/j"]
    assert tailor_argv("http://example.com/j") == ["--url", "http://example.com/j"]


def test_tailor_argv_pasted_text():
    assert tailor_argv("Senior iOS Engineer at Acme") == [
        "--job-text",
        "Senior iOS Engineer at Acme",
    ]


# ---- is_stale -------------------------------------------------------------
def test_is_stale_old():
    assert is_stale(1000, 1000 + 601) is True


def test_is_stale_recent():
    assert is_stale(1000, 1000 + 599) is False


def test_is_stale_boundary_is_not_stale():
    # exactly max_age old is not > max_age
    assert is_stale(1000, 1000 + 600) is False


# ---- stderr_tail ----------------------------------------------------------
def test_stderr_tail_empty():
    assert stderr_tail("") == ""
    assert stderr_tail(None) == ""


def test_stderr_tail_returns_last_lines_only():
    text = "\n".join("line{}".format(i) for i in range(50))
    tail = stderr_tail(text)
    assert "line49" in tail
    assert "line10" not in tail  # early lines dropped (only last 15 kept)


def test_stderr_tail_drops_blank_lines():
    assert stderr_tail("\n\n  \nreal error\n\n") == "real error"


# ---- format_duration ------------------------------------------------------
def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(3661) == "1h 1m"


# ---- iso_utc --------------------------------------------------------------
def test_iso_utc_epoch_zero():
    out = iso_utc(0)
    assert out.startswith("1970-01-01")
    assert out.endswith("UTC")


# ---- render_status --------------------------------------------------------
def test_render_status_absent():
    out = render_status(None, in_progress=False, uptime="1h 2m", now_iso="2026-07-04 10:00 UTC")
    assert "no pipeline runs" in out.lower()
    assert "Uptime: 1h 2m" in out


def test_render_status_in_progress():
    last = {"trigger": "run", "started": "2026-07-04T09:00:00Z", "finished": None, "exit_code": None}
    out = render_status(last, in_progress=True, uptime="5m 0s", now_iso="2026-07-04 10:00 UTC")
    assert "in progress" in out.lower()
    assert "run" in out


def test_render_status_present_success():
    last = {"trigger": "timer", "started": "s", "finished": "f", "exit_code": 0}
    out = render_status(last, in_progress=False, uptime="5m 0s", now_iso="n")
    assert "timer" in out
    assert "✅" in out


def test_render_status_present_failure():
    last = {"trigger": "run", "started": "s", "finished": "f", "exit_code": 1}
    out = render_status(last, in_progress=False, uptime="5m 0s", now_iso="n")
    assert "❌" in out
    assert "exit 1" in out
