# Running the daily flow on a Raspberry Pi

This runs the full pipeline — **fetch → dedupe → LLM filter → tailor résumé →
validate → compile and verify a one-page PDF → Telegram** — on a self-hosted Pi
instead of (or alongside) the GitHub Actions cron.

> The GitHub Actions cron is still the more reliable option (no SD-card wear, no
> home power/network dependency). Treat the Pi as a project or a redundant runner
> and keep the Actions workflow as your fallback.

---

## Will it run on my Pi?

The whole application is **pure Python standard library** — the LLM calls
(Gemini/Qwen) and Telegram delivery are raw `urllib` HTTPS requests, and LaTeX is
a `subprocess` call to `xelatex`. There is **no `grpcio`, no `google-generativeai`
SDK, no `requests`** — nothing to compile. So the full pipeline needs **zero pip
packages**; only the optional sources do.

| Feature | Original Pi B (ARMv6, ~512 MB) | Notes |
|---|---|---|
| 16 stdlib sources + LLM filter + tailor + PDF + Telegram | ✅ Works, no pip installs | All stdlib; LLM/Telegram are HTTPS |
| `linkedin-guest` (LinkedIn via the public guest API) | ✅ Works, stdlib | Default-off; the Pi's `.env` turns it on **in place of** the jobspy LinkedIn sources |
| `jobspy` (Indeed + Google) | ⚙️ Opt-in via a bundled lib | `tls-client` has no ARMv6 wheel, so the repo ships a cross-built `vendor/tls-client-armv6.so`; `scripts/enable-jobspy.sh` wires it up |
| `linkedin-global` / `linkedin-israel` (jobspy LinkedIn) | ➖ Superseded | `linkedin-guest` replaces them; the Pi's `.env` disables them |
| `secrettelaviv` (Chromium) | ❌ Skip | Chromium has no ARMv6 build |

The optional sources are **lazily imported**: absent their dependency, each
registered source self-skips at fetch time and everything else runs. **20 sources
are registered.** The setup script's `.env` enables `linkedin-guest` and disables
the two jobspy LinkedIn sources, selecting **18** — of which **16 stdlib sources
actually fetch** on a stock Pi (`jobspy` and `secrettelaviv` self-skip without
their optional deps). Running `scripts/enable-jobspy.sh` adds Indeed/Google for a
17th. On a Pi 3/4/5 (ARMv7/ARMv8) the jobspy sources install cleanly via piwheels
and Chromium is available, so you can run all of it.

The code uses no Python 3.10+ syntax, so whatever Python ships with Raspberry Pi
OS (3.9 on Bullseye, 3.11 on Bookworm) is fine — no need to compile 3.12.

---

## How long does a run take?

The Pi's slow CPU barely matters for most stages — fetch and every LLM call are
network-bound (the work happens in the cloud). The **only** place a 700 MHz ARMv6
core bites is the `xelatex` compiles during tailoring.

| Stage | Bound by | Time on Pi B |
|---|---|---|
| Fetch (16 concurrent sources, capped by `SCRAPE_BUDGET_SECONDS`) | Network | ~2–5 min (hard ceiling 10 min) |
| Dedupe | trivial | seconds |
| Filter — **1 Gemini call per _new_ job**, `EVAL_WORKERS` at a time | Gemini latency | ~1–4 min typical |
| Tailor — per **match**: 1 long LLM call + `xelatex` ×2 per attempt (≤3 attempts, 120 s each) | xelatex on ARMv6 + LLM | **~1.5–2.5 min per match** |
| Notify | Network | seconds |

- **Steady-state day** (~30 new jobs, ~3 matches): **~10–20 minutes**, dominated
  by the xelatex compiles.
- **Heavy day** (10 matches): tailoring pushes it to ~20–30 minutes.
- **First run on empty state**: every fetched job is "new" → hundreds of LLM
  evals and dozens of compiles → **potentially 1–3 hours**. Seed the dedup state
  (below) so the first run behaves like a normal day.

---

## Quickstart (one script)

1. **Flash the OS**: Raspberry Pi Imager → **Raspberry Pi OS Legacy Lite
   (Bullseye), 32-bit** (only the 32-bit build supports ARMv6; Lite has no desktop
   to eat your RAM). In ⚙️ settings enable **SSH**, hostname, Wi-Fi, and locale.

2. **Clone and run the setup script** on the Pi:
   ```bash
   git clone https://github.com/dsx87/job-search.git ~/job-search
   cd ~/job-search
   bash scripts/setup-rpi.sh
   ```
   Override defaults with env vars if needed:
   ```bash
   TIMEZONE=Europe/Berlin RUN_TIME=06:30 SWAP_MB=2048 bash scripts/setup-rpi.sh
   TRY_JOBSPY=1 bash scripts/setup-rpi.sh     # also attempt the 3 JobSpy sources
   ```

The script installs packages, bumps swap, sets the timezone, writes a `.env`
template, seeds `seen_jobs.json` from the `state` branch, pre-warms xelatex, and
installs the systemd service + timer. It **stops short** of putting in your
secrets and enabling the timer — finish those two steps below.

> The script must already be committed to the repo for `git clone` to bring it to
> the Pi. If you're setting this up before pushing, `scp scripts/setup-rpi.sh`
> over instead.

3. **Fill in secrets** (the script created `~/job-search/.env`, mode 600):
   ```bash
   nano ~/job-search/.env
   ```
   | Variable | Required | Purpose |
   |---|---|---|
   | `GEMINI_API_KEY` | ✅ | LLM filtering & tailoring |
   | `TELEGRAM_BOT_TOKEN` | ✅ | delivery |
   | `TELEGRAM_CHAT_ID` | ✅ | delivery |
   | `QWEN_API_KEY` | optional | fallback model |
   | `CV_PHONE` | optional | phone injected into the CV at compile time |
   | `EVAL_WORKERS` / `TAILOR_WORKERS` | tuning | keep low on a single core (2 / 1) |
   | `SCRAPE_BUDGET_SECONDS` | tuning | fetch-stage wall-clock ceiling (default 600) |
   | `SOURCES_ENABLE` / `SOURCES_DISABLE` | sources | comma lists forcing sources on/off; the Pi ships `linkedin-guest` on and the jobspy LinkedIn sources off |
   | `STATE_SYNC` | sync | `1` to sync `seen_jobs.json` with the `state` branch (see below); default `0` |

4. **Smoke-test** the heaviest path end to end (fetch → tailor → PDF → Telegram):
   ```bash
   cd ~/job-search && set -a && . ./.env && set +a
   python3 -m job_search.pipeline --tailor \
     --job-text 'Senior iOS Engineer, remote, Swift/SwiftUI' \
     --title 'Senior iOS Developer' --company 'Acme'
   ```
   A tailored PDF landing in Telegram means the whole strict chain worked:
   factual validation, XeLaTeX compilation, one-page verification, and PDF
   upload all succeeded.

5. **Enable the daily timer**:
   ```bash
   sudo systemctl enable --now job-search.timer
   systemctl list-timers job-search.timer      # confirm next run
   ```

---

## Seeding the dedup state (avoid the first-run trap)

`seen_jobs.json` is **not** on `main`; the Actions run keeps it on the orphan
`state` branch. On a persistent Pi you just keep the file locally
(`SEEN_JOBS_FILE` defaults to a relative `seen_jobs.json`, read/written from the
repo root). The setup script seeds it automatically; to do it by hand:

```bash
cd ~/job-search
git fetch origin state --depth 1
git show origin/state:seen_jobs.json > seen_jobs.json
```

If you skip this, the first run treats every scraped job as new (hours of work).

---

## Syncing the dedup state (share it with the Actions runner)

Seeding is one-shot. To stop the Pi and the GitHub Actions runner from
re-delivering each other's jobs, **sync** `seen_jobs.json` both ways with the
orphan `state` branch: the daily run pulls it before fetching and pushes the
updated file back after. The two run staggered (Pi 10:00, Actions 14:00 Israel),
so whichever runs second inherits the first's dedup baseline.

1. Run the one-time setup — creates a dedicated SSH deploy key, the `github-state`
   host alias, and a `.state` checkout of the `state` branch:
   ```bash
   bash ~/job-search/scripts/setup-state-sync.sh
   ```
2. Add the printed public key as a **write** deploy key on
   `github.com/dsx87/job-search` (Settings → Deploy keys → Add, tick *Allow write
   access*).
3. Turn it on:
   ```bash
   echo 'STATE_SYNC=1' >> ~/job-search/.env
   ```

The next run brackets the fetch with the sync — look for these in the journal:
```
[state] pulled 1234 keys from origin/state
Scraping 18 sources: ...
[state] pushed 1240 keys on attempt 1
```
Sync is best-effort: if GitHub is unreachable or the key isn't set up, the run
proceeds on the local `seen_jobs.json`, and a push failure is logged but never
fails the run (the state is left local and retried next time). A concurrent push
from the other runner is resolved by union-merging both files, so no keys are lost.

---

## Telegram control bot

Because the home network has **no dedicated IP**, there's no webhook or port
forward. The bot instead **long-polls** Telegram's `getUpdates` — all traffic is
outbound HTTPS, so it works behind NAT with nothing to open on your router. The
setup script installs it as `job-search-bot.service` (`Type=simple`,
`Restart=always`), enabled alongside the timer once your secrets are in.

From the **authorized chat only** (`TELEGRAM_CHAT_ID` — messages from any other
account are ignored silently):

| Command | What it does |
|---|---|
| `/run` | Kick off a full pipeline run now. You get a "Started" ack, then a completion message with the duration (or the error). |
| `/status` | Report whether a run is in progress or idle, the last run's trigger / exit code / timestamps, and the bot's uptime. |
| `/tailor <url>` | Tailor a CV against a job posting URL (auto-fetched) and send the PDF. |
| `/tailor <pasted description>` | Same, but from a pasted job description (the paste fallback for login-walled or JS-only URLs). |

The bot **self-registers** this command menu with Telegram via `setMyCommands`
on startup, so autocomplete works with no @BotFather step.

Daily runs, CLI `--tailor`, and bot `/tailor` all use the same fail-closed CV
path. The pipeline tries one factual correction and repairs eligible compiler
errors, but unresolved validation, compilation, page-verification, or upload
failures block artifact delivery. Scheduled fits remain unseen for a full retry
and the final run summary reports preparation, notification, CV-delivery, and
failure counts. The system never sends raw `.tex`, an unknown-page PDF, or a
multi-page PDF.

**Everything runs through one wrapper.** Both the daily timer and the bot execute
`scripts/run_pipeline.sh`, guarded by a single `flock` — the single 700 MHz core
never runs two pipelines at once. A second trigger while a run is active is
*refused, not queued*: `/run` during an active run replies "already in progress,"
and if the timer and a `/run` collide the wrapper simply exits 75 (visible in the
journal, harmless). The wrapper records each run in `.last_run.json` (what
`/status` reads) and tees full output to `logs/run-*.log` (newest 7 kept).

A **10-minute staleness guard** means a `/run` you queued during an outage won't
suddenly fire when the Pi reboots hours later — old messages are acknowledged but
not executed. (The Pi B has no RTC, so right after a power cut the clock is wrong
until NTP syncs; `After=network-online.target` mitigates this.)

## Operating it

```bash
# Run the daily job right now (out of schedule) — goes through the wrapper
sudo systemctl start job-search.service

# Watch a run's logs live
journalctl -u job-search.service -f

# Watch the control bot (command handling, poll errors)
journalctl -u job-search-bot.service -f

# When did / will the daily run fire?
systemctl list-timers job-search.timer

# Pause the daily run / stop the bot
sudo systemctl disable --now job-search.timer
sudo systemctl disable --now job-search-bot.service
```

The daily service and the bot both run from the repo root as your user, with
`.env` loaded via `EnvironmentFile` and `Nice=10` (stay responsive); the daily
run keeps its 2 h `TimeoutStartSec` safety cap. The run record lives in
`.last_run.json` (trigger, start/finish, exit code) with the full transcript in
`logs/run-*.log`.

---

## Manual setup (what the script does, for reference/troubleshooting)

```bash
# 1. Packages — right-sized TeX (NOT texlive-full, which is ~4 GB)
sudo apt update
sudo apt install -y --no-install-recommends git ca-certificates python3 python3-venv \
  texlive-xetex texlive-latex-recommended texlive-latex-extra \
  texlive-fonts-recommended fonts-crosextra-carlito fontconfig

# 2. Swap — pandas/xelatex/pip spike past 512 MB
sudo dphys-swapfile swapoff
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup && sudo dphys-swapfile swapon

# 3. Timezone
sudo timedatectl set-timezone Asia/Jerusalem

# 4. Pre-warm xelatex (first cold compile builds the font cache and can be slow)
cd ~/job-search && xelatex -interaction=nonstopmode igor_pivnyk_cv_base_updated.tex
```

The systemd unit files the script installs are `/etc/systemd/system/job-search.service`
and `.timer` — see the Quickstart output or the script source for their contents.

---

## Optional: reclaim the jobspy Indeed/Google source

LinkedIn is already covered by the stdlib `linkedin-guest` source, so the only
extra reach jobspy buys on the Pi is **Indeed + Google**. jobspy can't install out
of the box on ARMv6 — its Indeed scraper needs `tls-client`, whose Go shared
library ships no 32-bit ARM build — so the repo bundles a cross-built one
(`vendor/tls-client-armv6.so`, tuned for the Pi's arm1176jzf-s core). One script
wires it up:

```bash
bash ~/job-search/scripts/enable-jobspy.sh
```

It creates a `.venv`, installs `python-jobspy` from piwheels, and patches the
installed `tls_client` package to load the bundled `.so`. `run_pipeline.sh` then
picks up `.venv/bin/python` automatically — no unit edit needed. The full Indeed
matrix is slow on one ARMv6 core and may hit `SCRAPE_BUDGET_SECONDS` and be
abandoned; raise it in `.env` if you want it to finish. Turn it back off with
`rm -rf ~/job-search/.venv` (the `jobspy` source then self-skips and everything
else keeps running on system python3).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `xelatex not found` in logs | TeX install failed — re-run the apt step; check `which xelatex`. |
| CV compile fails on `\setmainfont{Carlito}` | Font not registered: `sudo apt install fonts-crosextra-carlito && fc-cache -f`, then `fc-list \| grep -i carlito`. |
| First compile hits the 120 s timeout | Pre-warm once by hand (step 4) so the cache is already built. |
| Runs get OOM-killed | Increase swap (`SWAP_MB=2048`), and keep `TAILOR_WORKERS=1` / `EVAL_WORKERS=2`. |
| TLS/certificate errors to Gemini/Telegram | `sudo apt install ca-certificates && sudo update-ca-certificates`. |
| First run takes hours | You didn't seed `seen_jobs.json` — see "Seeding the dedup state". |
| Fetch hangs | Expected occasionally (LinkedIn throttling); `SCRAPE_BUDGET_SECONDS` caps the fetch stage so the run continues. |
| A matching job has no CV | Check the final run summary and logs for validation, preparation, or delivery failures; failed fits remain unseen and retry the full flow next run. |
