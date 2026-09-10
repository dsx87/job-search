# Running the daily flow on a Raspberry Pi

This runs the full pipeline — **fetch → dedupe → LLM filter → tailor résumé →
validate → compile and verify a one-page PDF → Telegram** — on a self-hosted Pi
instead of (or alongside) the GitHub Actions cron.

This guide describes the private-configuration deployment after its public code
branch has been manually merged. Preparing the branch does not change an
existing Actions schedule, Pi timer, bot, or delivery path.

> The GitHub Actions cron is still the more reliable option (no SD-card wear, no
> home power/network dependency). Treat the Pi as a project or a redundant runner
> and keep the Actions workflow as your fallback.

---

## Will it run on my Pi?

The whole application is **pure Python standard library** — every LLM call
(each provider is a raw wire-protocol scheme) and Telegram delivery is a raw
`urllib` HTTPS request, and LaTeX is
a `subprocess` call to `pdflatex`. There is **no `grpcio`, no `google-generativeai`
SDK, no `requests`** — nothing to compile. Python 3.11+ uses its standard
library TOML parser; Python 3.9/3.10 needs the small `tomli` package, which
`scripts/setup-rpi.sh` installs. Optional sources may need additional packages.

| Feature | Original Pi B (ARMv6, ~512 MB) | Notes |
|---|---|---|
| 16 stdlib sources + LLM filter + tailor + PDF + Telegram | ✅ Works | `tomli` is installed on Python 3.9/3.10; LLM/Telegram are HTTPS |
| `linkedin-guest` (LinkedIn via the public guest API) | ✅ Works, stdlib | Select it in the pinned private TOML when needed |
| `jobspy` (Indeed + Google) | ⚙️ Opt-in via a bundled lib | `tls-client` has no ARMv6 wheel, so the repo ships a cross-built `vendor/tls-client-armv6.so`; `scripts/enable-jobspy.sh` wires it up |
| `linkedin-global` / `linkedin-israel` (jobspy LinkedIn) | optional | Select sources in the pinned private TOML |
| `secrettelaviv` (Chromium) | ❌ Skip | Chromium has no ARMv6 build |

The optional sources are **lazily imported**: absent their dependency, each
registered source self-skips at fetch time and everything else runs. **20 sources
are registered. Select sources in the pinned private TOML. On a stock Pi,
sources whose optional dependencies are absent self-skip; running
`scripts/enable-jobspy.sh` makes the JobSpy sources available. On a Pi 3/4/5
(ARMv7/ARMv8), JobSpy installs cleanly via piwheels and Chromium is available.

The code uses no Python 3.10+ syntax, so whatever Python ships with Raspberry Pi
OS (3.9 on Bullseye, 3.11 on Bookworm) is fine — no need to compile 3.12.

---

## How long does a run take?

The Pi's slow CPU barely matters for most stages — fetch and every LLM call are
network-bound (the work happens in the cloud). The **only** place a 700 MHz ARMv6
core bites is the `pdflatex` compiles during tailoring.

| Stage | Bound by | Time on Pi B |
|---|---|---|
| Fetch (16 concurrent sources, capped by `SCRAPE_BUDGET_SECONDS`) | Network | ~2–5 min (hard ceiling 10 min) |
| Dedupe | trivial | seconds |
| Filter — **1 LLM call per _new_ job**, `EVAL_WORKERS` at a time | LLM latency | ~1–4 min typical |
| Tailor — per **match**: 1 long LLM call + `pdflatex` ×2 per attempt (≤3 attempts, 120 s each) | pdflatex on ARMv6 + LLM | **~1.5–2.5 min per match** |
| Notify | Network | seconds |

- **Steady-state day** (~30 new jobs, ~3 matches): **~10–20 minutes**, dominated
  by the pdflatex compiles.
- **Heavy day** (10 matches): tailoring pushes it to ~20–30 minutes.
- **First run on empty state**: every fetched job is "new" → hundreds of LLM
  evals and dozens of compiles → **potentially 1–3 hours**. Seed the dedup state
  (below) so the first run behaves like a normal day.

---

## Quickstart (one script)

Before provisioning, create a private configuration repository. It contains the
deployment TOML, CV source, prompt files, and sections; it must never contain
credentials. Commit those files, record the full lowercase 40-character commit
SHA, and authorize a dedicated **read-only** deploy key for that repository.
The Pi key defaults to `~/.ssh/job_search_config_ed25519`.

1. **Flash the OS**: Raspberry Pi Imager → **Raspberry Pi OS Legacy Lite
   (Bullseye), 32-bit** (only the 32-bit build supports ARMv6; Lite has no desktop
   to eat your RAM). In ⚙️ settings enable **SSH**, hostname, Wi-Fi, and locale.

2. **Clone and run the setup script** on the Pi:
   ```bash
   git clone https://github.com/dsx87/job-search.git ~/job-search
   cd ~/job-search
   cp deployment.env.example .deployment.env
   chmod 600 .deployment.env
   # Set CONFIG_REPOSITORY, CONFIG_REF, and CONFIG_SSH_KEY in .deployment.env.
   bash scripts/setup-rpi.sh
   ```
   Override defaults with env vars if needed:
   ```bash
   TIMEZONE=Europe/Berlin RUN_TIME=06:30 SWAP_MB=2048 bash scripts/setup-rpi.sh
   TRY_JOBSPY=1 bash scripts/setup-rpi.sh     # also attempt the 3 JobSpy sources
   ```

The script installs packages, bumps swap, sets the timezone, writes a missing
`.env` template without changing an existing one, synchronizes the exact private
revision into `.private-config/job-search-config`, validates its TOML, seeds
`seen_jobs.json`, pre-warms the configured CV, and writes systemd units. It does
not start, stop, enable, disable, or restart the timer or bot.

> The script must already be committed to the repo for `git clone` to bring it to
> the Pi. If you're setting this up before pushing, `scp scripts/setup-rpi.sh`
> over instead.

3. **Fill in secrets** (the script created `~/job-search/.env`, mode 600):
   ```bash
   nano ~/job-search/.env
   ```
   | Variable | Required | Purpose |
   |---|---|---|
   | `LLM_PRIMARY_API_KEY` | ✅ | primary provider key (also read as `GEMINI_API_KEY` for back-compat) |
   | `TELEGRAM_BOT_TOKEN` | ✅ | delivery |
   | `TELEGRAM_CHAT_ID` | ✅ | delivery |
   | `LLM_FALLBACK_API_KEY` | optional | fallback provider key (e.g. a prepaid OpenAI key) |
   | `LLM_PRIMARY_SCHEME` / `LLM_PRIMARY_MODEL` | optional | primary scheme (default `gemini`) + model (default `gemini-2.5-flash`) |
   | `LLM_PRIMARY_API_BASE` | optional | primary API base override; an absolute non-default HTTP(S) URL is required with `LLM_PRIMARY_AUTH_MODE=none` |
   | `LLM_PRIMARY_AUTH_MODE` | optional | `bearer` (default) or explicit `none` for a trusted local OpenAI-compatible server |
   | `LLM_FALLBACK_SCHEME` / `LLM_FALLBACK_MODEL` | optional | fallback scheme (default `openai`) + model (default `gpt-5.4-mini`) |
   | `LLM_FALLBACK_API_BASE` | optional | fallback API base override (e.g. `https://api.groq.com/openai/v1`); an absolute non-default HTTP(S) URL is required with fallback `none` auth |
   | `LLM_FALLBACK_AUTH_MODE` | optional | fallback `bearer` / explicit `none` mode |
   | `JOB_SEARCH_CONFIG_FILE` | optional | absolute path to a trusted escape-hatch module; an optional repo-root `job_search_config.py` is used when unset. **Setting it to an empty value is an error**, not "disabled" — it doesn't silently fall back |
   | `CV_PHONE` | optional | phone injected into the CV at compile time |
   | `EVAL_WORKERS` / `TAILOR_WORKERS` | tuning | keep low on a single core (2 / 1) |
   | `SCRAPE_BUDGET_SECONDS` | tuning | fetch-stage wall-clock ceiling (default 600) |
   | `SOURCES_ENABLE` / `SOURCES_DISABLE` | sources | non-secret host overrides; normally select sources in the pinned TOML |
   | `STATE_SYNC` | sync | `1` to sync `seen_jobs.json` with the `state` branch (see below); default `0` |
   | `OUTPUT_MODE` / `OUTPUT_DIR` / `OUTPUT_CV_MODE` | optional | non-Telegram delivery (`html`/`plain` to a directory); `telegram` (default) requires `OUTPUT_CV_MODE=required` |
   | `PROMPT_DIR` / `PROMPT_REVISION` | optional | file-backed prompt overrides; `PROMPT_REVISION` is required whenever `PROMPT_DIR` is set |
   | `LATEX_ENGINE` | optional | LaTeX executable to compile the CV (default `pdflatex`) |

   A provider is a **scheme** (`gemini` \| `openai` \| `anthropic`) + model + key
   (+ optional base) — switching providers is a config edit, no code change. The
   `openai` scheme covers any OpenAI-compatible endpoint (Groq, xAI Grok,
   DeepSeek, …) via its `*_API_BASE`. Leave provider overrides unset, empty, or
   whitespace-only to use the defaults; API-base overrides may include a trailing
   slash (the client normalizes it).

   > ⚠️ The default primary `gemini-2.5-flash` is scheduled for shutdown on
   > **2026-10-16** (kept deliberately until then — it is steadier here than the
   > 3.x lineage). `LLM_FALLBACK_API_KEY` is *not* optional in practice: with it
   > blank, a retired primary turns every job into an evaluation failure and the
   > Pi delivers nothing. The run log warns from 120 days out.

   For local inference on this same host, use the `openai` scheme with the
   server's loopback URL and `LLM_PRIMARY_AUTH_MODE=none`. No-auth mode is
   explicit: it omits the Authorization header but keeps JSON-schema structured
   output. See [`configuration.md`](configuration.md#local-openai-compatible-inference)
   for an LM Studio example. A URL on another machine must be reachable from
   the Pi; `127.0.0.1` always means the Pi itself.

### The escape hatch on the Pi

Almost everything above is a setting; no `job_search_config.py` is required.
For the rare thing that genuinely needs code, keep a reviewed hook on the Pi
and point `JOB_SEARCH_CONFIG_FILE` at it explicitly. The pinned private
configuration checkout supplies TOML only; it does not implicitly execute a
Python hook. A hook is trusted, deliberately unvalidated executable Python —
keep tokens and private CV values in the mode-600 `.env`, never in that module.
If you replace the
`.venv` `scripts/setup-rpi.sh` creates, reinstall `pyzipper~=0.4.0` (used by
Telegraph digest delivery) plus anything your module imports. See
[`configuration.md`](configuration.md#job_search_configpy-the-escape-hatch).

4. **Verify the local deployment without delivery**:
   ```bash
   cd ~/job-search
   scripts/prepare-private-config.sh --check-only
   CONFIG_TOML="$PWD/.private-config/job-search-config/job_search.toml"
   JOB_SEARCH_SETTINGS_FILE="$CONFIG_TOML" \
     .venv/bin/python -m job_search.pipeline --validate-config "$CONFIG_TOML"
   ```
   `--check-only` performs no fetch and never exposes configuration contents.

5. **Choose activation explicitly** after reviewing the validated configuration:
   ```bash
   sudo systemctl enable --now job-search.timer
   systemctl list-timers job-search.timer      # confirm next run
   ```

## Update or roll back a private configuration

`scripts/prepare-private-config.sh --sync` is the only command that fetches the
private repository. It rejects a mismatched origin, local changes, a non-SHA
pin, or a checkout that cannot resolve the requested commit. `--check-only` is
read-only and network-free. Neither command rewrites `.env`.

To roll back, select a known compatible pair: the public code revision and the
private configuration SHA. Check out the public revision, put the paired SHA in
`.deployment.env`, run `--sync`, then run `--check-only`. Do not use `pull`,
`reset`, `clean`, or a branch name to move the private checkout.

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
setup script writes it as `job-search-bot.service` (`Type=simple`,
`Restart=always`) without changing whether it is enabled or running.

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
failures block artifact delivery. Scheduled fits make no more than three
automated delivery attempts: the initial day, one day later, and two days after
that (days 0, 1, and 3). While backoff is pending, the job performs no LLM work.

When a fit is known but its PDF cannot be prepared, Telegram sends one
“verified CV pending” message containing the fit reasoning and next retry date.
That successful text delivery is recorded immediately. Later attempts skip
evaluation and send only a newly verified PDF, so a failed upload never repeats
the fit notification. After attempt three, the job enters a blocked state and a
terminal alert points to `/tailor`; terminal alerts retry until Telegram accepts
them without repeating any LLM work. `/tailor` is intentionally manual and
bypasses this daily state. The final summary reports retry, backoff, known-fit,
blocked, preparation, notification, and CV-delivery counts plus bounded per-job
failure details. The system never sends raw `.tex`, an unknown-page PDF, or a
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
`.deployment.env` loaded before `.env` and `Nice=10` (stay responsive); the
daily run keeps its 2 h `TimeoutStartSec` safety cap. The run record lives in
`.last_run.json` (trigger, start/finish, exit code) with the full transcript in
`logs/run-*.log`.

---

## Manual setup (what the script does, for reference/troubleshooting)

```bash
# 1. Packages — right-sized TeX (NOT texlive-full, which is ~4 GB). Helvetica
#    ships in texlive-fonts-recommended; pdflatex comes in as a dependency.
sudo apt update
sudo apt install -y --no-install-recommends git ca-certificates python3 python3-venv \
  texlive-latex-recommended texlive-fonts-recommended

# 2. Swap — pandas/pdflatex/pip spike past 512 MB
sudo dphys-swapfile swapoff
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup && sudo dphys-swapfile swapon

# 3. Timezone
sudo timedatectl set-timezone Asia/Jerusalem

# 4. Verify the pinned configuration and pre-warm its configured CV
cd ~/job-search && scripts/prepare-private-config.sh --check-only
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
| `pdflatex not found` in logs | TeX install failed — re-run the apt step; check `which pdflatex`. |
| First compile hits the 120 s timeout | Pre-warm once by hand (step 4) so the cache is already built. |
| Runs get OOM-killed | Increase swap (`SWAP_MB=2048`), and keep `TAILOR_WORKERS=1` / `EVAL_WORKERS=2`. |
| TLS/certificate errors to Gemini/Telegram | `sudo apt install ca-certificates && sudo update-ca-certificates`. |
| First run takes hours | You didn't seed `seen_jobs.json` — see "Seeding the dedup state". |
| Fetch hangs | Expected occasionally (LinkedIn throttling); `SCRAPE_BUDGET_SECONDS` caps the fetch stage so the run continues. |
| A matching job has no CV | Check the final summary for its attempt and next retry date. Automated attempts run on days 0, 1, and 3; after the terminal blocked alert, use `/tailor <url>` (or paste the description) to recover manually. |
