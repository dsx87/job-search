# Running the daily flow on a Raspberry Pi

This runs the full pipeline — **fetch → dedupe → LLM filter → tailor résumé →
compile PDF → Telegram** — on a self-hosted Pi instead of (or alongside) the
GitHub Actions cron.

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
| 3 JobSpy sources (JobSpy, LinkedIn ×2) | ⚠️ Usually won't install | `python-jobspy` → `pydantic-core` (Rust) + `tls-client` have no ARMv6 wheel; LinkedIn rate-limits anyway |
| 1 Chromium source (`secrettelaviv`) | ❌ Skip | Chromium has no ARMv6 build |

The optional sources are **lazily imported**, so if their dependencies are absent
the source registry simply drops them and everything else runs. On an original Pi
B you realistically get **16 of ~20 sources** plus the entire filter/tailor/notify
path. On a Pi 3/4/5 (ARMv7/ARMv8) the JobSpy sources install cleanly via piwheels
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

4. **Smoke-test** the heaviest path end to end (fetch → tailor → PDF → Telegram):
   ```bash
   cd ~/job-search && set -a && . ./.env && set +a
   python3 -m job_search.pipeline --tailor \
     --job-text 'Senior iOS Engineer, remote, Swift/SwiftUI' \
     --title 'Senior iOS Developer' --company 'Acme'
   ```
   A tailored PDF landing in Telegram means the whole chain works.

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

## Optional: reclaim the 3 JobSpy sources

Only worth it for LinkedIn coverage, and it usually fails on ARMv6:

```bash
cd ~/job-search && python3 -m venv .venv && . .venv/bin/activate
pip install python-jobspy    # if pydantic-core / tls-client won't build, just stop
```

If it installs, point the service's `ExecStart` at `~/job-search/.venv/bin/python`
(or re-run `TRY_JOBSPY=1 bash scripts/setup-rpi.sh`, which does this for you). If
it doesn't, walk away — you keep all 16 other sources.

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
