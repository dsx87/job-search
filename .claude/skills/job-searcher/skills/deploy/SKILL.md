---
name: deploy
description: Deploy and operate the AI Job Hunter — the GitHub Actions daily cron, the Raspberry Pi systemd + Telegram-bot install, dedup-state seeding and sync, and rollout of a config change to a running host.
when_to_use: Triggered by requests like "set up the daily job search on Actions", "deploy this to the Pi", "why did last night's run fail", "the timer didn't fire", "push my config change to the Pi", "seed the dedup state".
argument-hint: [actions | pi | rollout | triage]
disable-model-invocation: true
---

# Deploy and operate the job searcher

Two runners execute the identical fetch → dedupe → filter → tailor → notify
chain. Pick the target first, then follow only that track.

| | GitHub Actions | Raspberry Pi 1 (ARMv6) |
|---|---|---|
| Setup | repository secrets only | `bash scripts/setup-rpi.sh` |
| Sources | all ~20 (JobSpy + Chromium) | ~16 of 20 (stdlib-only path) |
| Trigger | daily cron 11:00 UTC + `workflow_dispatch` | `job-search.timer` + Telegram `/run` |
| State | orphan `state` branch | working directory, optionally synced |
| Guide | README "Deploy on GitHub Actions" | `docs/deploy-rpi.md` |

**Confirm before acting.** Enabling a timer, pushing secrets, restarting a unit,
or seeding state all have effects outside this repo. State what you are about to
do and get a yes first. Never print or echo a secret value.

## Track A — GitHub Actions

1. Fork or use the repo; the daily workflow is `.github/workflows/job_search.yml`
   (`Daily Job Search`). `render_base_cv.yml`, `tailor_cv.yml`,
   `verify_cv_one_page.yml` and `tests.yml` are the supporting workflows.
2. Add Actions **secrets**: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID` are required; `OPENAI_API_KEY` (fallback — effectively
   required, see below), `CV_PHONE`, `TELEGRAPH_ACCESS_TOKEN`, and
   `JOB_SEARCH_CONFIG_PY` are optional.
3. Add Actions **variables** only if overriding provider defaults or grouping the
   digest: the eight `LLM_*` names, and `SECTIONS_PY`.
4. Dispatch the workflow manually once and read the run log before trusting the
   cron.

The workflow reads `seen_jobs.json` from the orphan `state` branch and commits it
back there. A repository **ruleset that matches the `state` branch will break
that push with GH013** — check rulesets first when a run succeeds but state never
advances.

Verify with `gh run list --workflow job_search.yml` and
`gh run view <id> --log`.

## Track B — Raspberry Pi

```bash
git clone https://github.com/dsx87/job-search.git ~/job-search
cd ~/job-search
bash scripts/setup-rpi.sh          # idempotent; safe to re-run
nano .env                          # mode 600 — fill in the keys
sudo systemctl enable --now job-search.timer job-search-bot.service
```

The script installs a right-sized TeX (never `texlive-full`), bumps swap, sets
the timezone, writes the `.env` template, seeds `seen_jobs.json` from the `state`
branch, pre-warms `pdflatex`, and installs the units. It deliberately stops
short of secrets and of enabling the timer.

Overrides: `TIMEZONE=`, `RUN_TIME=`, `SWAP_MB=`, `TRY_JOBSPY=1`.

Pi-specific settings: `EVAL_WORKERS=2`, `TAILOR_WORKERS=1`, `linkedin-guest` on
via `SOURCES_ENABLE`, the jobspy LinkedIn sources off via `SOURCES_DISABLE`.

**Seed the dedup state before the first run.** On empty state every fetched job
is new — hundreds of LLM evaluations and dozens of compiles, potentially 1–3
hours. `python3 -m job_search.pipeline --seed` marks everything currently
fetched as seen without evaluating.

Optional two-way state sync with the Actions runner:
`bash scripts/setup-state-sync.sh`, add the printed key as a **write** deploy
key, then `STATE_SYNC=1` in `.env`. Sync is best-effort — a failure logs and the
run proceeds locally; concurrent pushes union-merge.

`docs/deploy-rpi.md` carries the hardware notes, runtime expectations, the
manual equivalent of every script step, and the troubleshooting table.

## Operating a Pi host

```bash
sudo systemctl start job-search.service        # run now, through the wrapper
journalctl -u job-search.service -f            # live run log
journalctl -u job-search-bot.service -f        # bot: commands, poll errors
systemctl list-timers job-search.timer         # last / next fire
sudo systemctl disable --now job-search.timer  # pause the daily run
```

Both the timer and the bot execute the same `flock`'d `scripts/run_pipeline.sh`,
so the single core never runs two pipelines; a colliding trigger is refused, not
queued. The run record is `.last_run.json` (trigger, start/finish, exit code);
the transcript is `logs/run-*.log`.

Telegram control bot (long-polls `getUpdates`, so it works behind NAT with
nothing opened on the router): `/run`, `/status`, `/tailor <url>`,
`/tailor <pasted text>` — from the authorized chat only.

## Rolling out a config change

1. Validate locally: `python3 -m job_search.pipeline --check-config`.
2. Actions: commit tracked files, or update the secret/variable — an untracked
   `sections.py` or `job_search_config.py` never reaches a runner.
3. Pi: `git pull` in `~/job-search`, edit `.env` or the untracked local files,
   then `--check-config` **on the Pi**, then trigger one run and watch it.
4. Confirm the digest actually arrived before calling it deployed.

## Triage

| Symptom | Where to look |
|---|---|
| Run green, state never advances | `state`-branch ruleset (GH013) on the push |
| Fetch hangs / run times out | LinkedIn throttling; `SCRAPE_BUDGET_SECONDS` caps the stage |
| Nothing delivered, every job failed evaluation | primary model retired or key missing; check the fallback key |
| `pdflatex not found`, or first compile times out | TeX install / cold cache — pre-warm once |
| OOM-killed on the Pi | raise `SWAP_MB`, keep `TAILOR_WORKERS=1` / `EVAL_WORKERS=2` |
| A match arrived with no CV | attempts run days 0, 1, 3; after the blocked alert recover with `/tailor` |
| First run takes hours | dedup state was never seeded |

The full Pi troubleshooting table is at the end of `docs/deploy-rpi.md`.
