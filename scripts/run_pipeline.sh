#!/usr/bin/env bash
#
# run_pipeline.sh — the single, flock'd entry point for EVERY pipeline run:
# the daily systemd timer, the Telegram /run and /tailor commands, and any
# manual invocation. Routing everything through one flock means the single
# 700 MHz core never runs two pipelines at once — a second caller is refused
# (exit 75), never queued.
#
# Usage:
#   scripts/run_pipeline.sh <trigger> [pipeline flags...]
#     <trigger>  one of: timer | run | tailor | manual — a label recorded in
#                .last_run.json (it is NOT forwarded to the pipeline). The daily
#                vs tailor/ad-hoc behavior is chosen by [flags], not the trigger.
#     [flags]    forwarded verbatim to `python -m job_search.pipeline`
#
# Exit codes:
#   0    the pipeline ran and exited 0
#   75   another run holds the lock (EX_TEMPFAIL) — "busy", distinct from failure
#   *    whatever the pipeline exited with
#
# NOTE: errexit is deliberately OFF. A nonzero pipeline exit must NOT abort the
# script before we record it in .last_run.json and prune old logs.
set -uo pipefail

trigger="${1:-manual}"
shift || true

# cd to the repo root (this script lives in <repo>/scripts/). The pipeline reads
# and writes RELATIVE paths — seen_jobs.json, .last_run.json, logs/, the CV
# files — so a fixed cwd makes them resolve no matter who spawned us (systemd,
# the bot, a shell).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
cd "$REPO" || { echo "run_pipeline: cannot cd to repo root '$REPO'" >&2; exit 1; }

# Resolve the interpreter ourselves so callers (the bot) never need to know
# PY_BIN: the jobspy venv if present, else the system python3.
if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
else
  PY="python3"
fi

# Load .env if present. systemd already injects it via EnvironmentFile, but this
# makes a manual `scripts/run_pipeline.sh …` from a bare shell self-sufficient
# and mirrors run.sh. There is no .env on CI — env there comes from the runner's
# secrets, and the `[ -f ]` guard simply skips this.
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a

LOCK="$REPO/.pipeline.lock"
LAST_RUN="$REPO/.last_run.json"
mkdir -p "$REPO/logs"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Atomically write .last_run.json (tmp + mv) so /status never reads a half file.
#   args: <started-json> <finished-json> <exit_code-json>
# Each arg is already a valid JSON value ("..."-quoted string, a number, or null).
write_last_run() {
  local started="$1" finished="$2" exit_code="$3" tmp
  tmp="$(mktemp "${LAST_RUN}.XXXXXX")"
  printf '{"trigger": "%s", "started": %s, "finished": %s, "exit_code": %s}\n' \
    "$trigger" "$started" "$finished" "$exit_code" > "$tmp"
  mv -f "$tmp" "$LAST_RUN"
}

# ---- Acquire the choke-point lock (non-blocking) ---------------------------
# FD 9 → the lock file; flock releases it automatically when the FD closes, even
# on crash or kill. util-linux flock exits 1 on contention; remap that to 75 so
# callers can tell "busy" apart from "the pipeline failed".
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "run_pipeline: another run is in progress (trigger was '$trigger') — refusing." >&2
  exit 75
fi

# ---- Record start, run the pipeline, record completion ---------------------
ts="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$REPO/logs/run-${ts}.log"
started="\"$(now_iso)\""
write_last_run "$started" "null" "null"

# One entry point for every host: the tracked package. Source selection
# (linkedin-guest) and dedup-state sync are now config-driven inside the package
# (SOURCES_ENABLE / SOURCES_DISABLE / STATE_SYNC in .env), so there is no longer
# a Pi-only run.sh / pi_run.py to dispatch to.
cmd=("$PY" -u -m job_search.pipeline "$@")

# Tee combined stdout+stderr to the log while keeping stderr on our own stderr
# (the bot captures stderr to relay the CLI's errors — e.g. the "insufficient
# job text" message run_tailor prints without a Telegram notification).
"${cmd[@]}" > >(tee -a "$LOG") 2> >(tee -a "$LOG" >&2)
rc=$?

finished="\"$(now_iso)\""
write_last_run "$started" "$finished" "$rc"

# Keep only the 7 newest run logs.
ls -1t "$REPO"/logs/run-*.log 2>/dev/null | tail -n +8 | xargs -r rm -f || true

exit "$rc"
