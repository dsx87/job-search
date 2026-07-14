"""Spawn the flock'd wrapper and report completion back to Telegram.

Boundaries — the subprocess launcher, the clock, and the thread spawner — are
injected so the runner is unit-testable offline. In production it spawns
``scripts/run_pipeline.sh`` and drains its stderr from a daemon watcher thread.
"""
import subprocess
import threading
import time

from .commands import escape, format_duration, stderr_tail


def _thread_spawn(target):
    """Default spawn: run the watcher on a daemon thread (dies with the bot)."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


class PipelineRunner:
    """Serializes bot-initiated runs and relays their outcome.

    An in-memory ``threading.Lock`` is the bot↔bot guard (a second /run while
    one is active is refused synchronously). The wrapper's flock is the
    cross-process guard against the daily timer; when that fires, the wrapper
    exits 75 and the watcher reports "already in progress" — the same
    user-facing outcome, reached a different way.
    """

    def __init__(self, wrapper_path, popen=subprocess.Popen, clock=time.time, spawn=_thread_spawn):
        self.wrapper_path = wrapper_path
        self._popen = popen
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()

    def is_running(self):
        """True while a bot-initiated run holds the in-memory lock."""
        return self._lock.locked()

    def start(self, trigger, extra_args, reply):
        """Spawn a run. Returns False (no spawn) if one is already active.

        On success a watcher thread drains the process and calls ``reply`` once
        with the outcome. ``reply`` must be safe to call from another thread.
        """
        if not self._lock.acquire(blocking=False):
            return False
        try:
            argv = [self.wrapper_path, trigger, *extra_args]
            proc = self._popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except BaseException:
            self._lock.release()
            raise
        started = self._clock()
        self._spawn(lambda: self._watch(proc, trigger, started, reply))
        return True

    def _watch(self, proc, trigger, started, reply):
        try:
            # communicate() drains stderr — no pipe deadlock, no zombie.
            _out, err = proc.communicate()
            rc = proc.returncode
            if rc == 75:
                reply(
                    "⏳ A run is already in progress (the daily timer is running) — "
                    "try again shortly."
                )
            elif rc == 0:
                duration = format_duration(self._clock() - started)
                reply("✅ Pipeline run finished OK in {} (trigger: {}).".format(duration, trigger))
            else:
                message = "❌ Pipeline run failed (exit {}, trigger: {}).".format(rc, trigger)
                tail = stderr_tail(err or "")
                if tail:
                    message += "\n<pre>{}</pre>".format(escape(tail))
                reply(message)
        finally:
            self._lock.release()
