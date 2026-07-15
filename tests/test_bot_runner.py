"""Tests for PipelineRunner with injected popen / spawn / clock (no real I/O)."""
import subprocess

from job_search.bot.runner import PipelineRunner


class FakeProc:
    """Minimal Popen stand-in: communicate() + returncode."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self._stderr = stderr
        self.communicated = False

    def communicate(self):
        self.communicated = True
        return ("", self._stderr)


def make_runner(proc, spawn=None, clock_values=(100.0, 190.0)):
    """Build a runner whose popen returns `proc`, recording the call."""
    calls = {"argv": None, "kwargs": None}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return proc

    def sync_spawn(target):
        target()  # run the watcher inline

    values = iter(clock_values)

    def clock():
        try:
            return next(values)
        except StopIteration:
            return clock_values[-1]

    runner = PipelineRunner(
        "/repo/scripts/run_pipeline.sh",
        popen=fake_popen,
        clock=clock,
        spawn=spawn or sync_spawn,
    )
    return runner, calls


def test_start_idle_spawns_and_replies_success():
    proc = FakeProc(returncode=0)
    runner, calls = make_runner(proc, clock_values=(100.0, 190.0))
    replies = []

    ok = runner.start("run", [], replies.append)

    assert ok is True
    assert calls["argv"] == ["/repo/scripts/run_pipeline.sh", "run"]
    assert calls["kwargs"]["stdout"] == subprocess.DEVNULL
    assert calls["kwargs"]["stderr"] == subprocess.PIPE
    assert proc.communicated is True
    assert len(replies) == 1
    assert "finished OK" in replies[0]
    assert "1m 30s" in replies[0]  # 190 - 100 = 90s
    assert runner.is_running() is False  # lock released after watcher


def test_second_start_while_busy_returns_false_no_second_spawn():
    proc = FakeProc(returncode=0)
    pending = []

    def deferred_spawn(target):
        pending.append(target)  # don't run yet → lock stays held

    runner, calls = make_runner(proc, spawn=deferred_spawn, clock_values=(0.0, 0.0))
    replies = []

    assert runner.start("run", [], replies.append) is True
    assert runner.is_running() is True
    first_argv = calls["argv"]

    # Second start while the first still holds the lock.
    assert runner.start("run", [], replies.append) is False
    assert calls["argv"] == first_argv  # popen not called a second time
    assert replies == []  # nothing replied yet

    # Draining the deferred watcher releases the lock.
    pending[0]()
    assert runner.is_running() is False
    assert len(replies) == 1


def test_exit_75_maps_to_already_in_progress():
    proc = FakeProc(returncode=75)
    runner, _calls = make_runner(proc)
    replies = []

    runner.start("run", [], replies.append)

    assert len(replies) == 1
    assert "already in progress" in replies[0].lower()
    assert runner.is_running() is False


def test_exit_nonzero_relays_stderr_tail():
    stderr = (
        "  Fetching job text from https://x\n"
        "Error: could not obtain enough job-description text "
        "(got 12 chars, need >= 200)."
    )
    proc = FakeProc(returncode=1, stderr=stderr)
    runner, _calls = make_runner(proc)
    replies = []

    runner.start("tailor", ["--tailor", "--url", "https://x"], replies.append)

    assert len(replies) == 1
    assert "exit 1" in replies[0]
    assert "could not obtain enough job-description text" in replies[0]


def test_tailor_argv_passthrough():
    proc = FakeProc(returncode=0)
    runner, calls = make_runner(proc)

    runner.start("tailor", ["--tailor", "--url", "https://x"], lambda _m: None)

    assert calls["argv"] == [
        "/repo/scripts/run_pipeline.sh",
        "tailor",
        "--tailor",
        "--url",
        "https://x",
    ]
