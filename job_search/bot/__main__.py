"""`python -m job_search.bot` — the composition root.

Wires config → transport → runner → poller and long-polls forever. systemd runs
this as a ``Type=simple`` service (``WorkingDirectory`` = the repo root, so the
relative paths line up with the wrapper's) with ``Restart=always``.
"""
import os
import signal
import sys
import time

from ..config import PipelineConfig
from ..notify.telegram import TelegramClient
from .poller import OffsetStore, Poller, set_my_commands, telegram_get_updates
from .runner import PipelineRunner

# Telegram long-poll window (seconds). The socket timeout is this + a margin
# (see telegram_get_updates), well under systemd's default stop timeout.
POLL_TIMEOUT_SECONDS = 50


def main():
    cfg = PipelineConfig.from_env()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.", file=sys.stderr)
        sys.exit(1)

    token = cfg.telegram_bot_token
    repo = os.getcwd()  # systemd WorkingDirectory; matches the wrapper's cwd
    wrapper = os.path.join(repo, "scripts", "run_pipeline.sh")

    # Register the autocomplete menu once (no @BotFather step). Best-effort.
    set_my_commands(token)

    telegram = TelegramClient(token, cfg.telegram_chat_id)
    runner = PipelineRunner(wrapper)
    offset_store = OffsetStore(os.path.join(repo, ".bot_offset"))

    def get_updates(offset):
        return telegram_get_updates(token, offset, POLL_TIMEOUT_SECONDS)

    poller = Poller(
        chat_id=cfg.telegram_chat_id,
        runner=runner,
        send=telegram.send_message,
        offset_store=offset_store,
        last_run_path=os.path.join(repo, ".last_run.json"),
        get_updates=get_updates,
        clock=time.time,
        sleep=time.sleep,
    )

    signal.signal(signal.SIGTERM, lambda *_: poller.stop())
    print("[bot] job-search control bot up (repo={}); long-polling…".format(repo), flush=True)
    poller.run()


if __name__ == "__main__":
    main()
