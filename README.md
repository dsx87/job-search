# AI Job Hunter

[![Daily Job Search](https://github.com/dsx87/job-search/actions/workflows/job_search.yml/badge.svg)](https://github.com/dsx87/job-search/actions/workflows/job_search.yml)
[![Tests](https://github.com/dsx87/job-search/actions/workflows/tests.yml/badge.svg)](https://github.com/dsx87/job-search/actions/workflows/tests.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An autonomous, self-hosted job-search agent. Every morning it scrapes ~20 job
boards, filters the results against my personal criteria with an LLM, tailors my
résumé to each matching role, compiles it to PDF, and delivers the matches —
with custom CVs attached — to Telegram.

**The same pipeline runs in two very different places:** a free **GitHub Actions**
cron (zero infrastructure) *and* a self-hosted **Raspberry Pi 1** — the original
700 MHz ARMv6 board with 512 MB of RAM. It runs on the Pi because the whole
application is **pure Python standard library**: there is nothing to compile and
zero pip packages to install for the core path.

> Built to run my own job search end-to-end. It's a working system, not a demo.

## Deploy it two ways

| | ☁️ GitHub Actions | 🍓 Raspberry Pi 1 (ARMv6) |
|---|---|---|
| **What** | A daily cron on GitHub's free runners | A self-hosted box on your desk |
| **Setup** | Add repo secrets, that's it | `bash scripts/setup-rpi.sh` |
| **Cost / infra** | Free, no server to operate | Your own hardware + home power |
| **Sources** | All ~20 (incl. JobSpy + Chromium) | ~16 of 20 (stdlib-only path) |
| **Trigger** | Daily cron + manual dispatch | systemd timer + Telegram `/run` |
| **Best as** | Primary / most reliable runner | A hardware project or a redundant runner |
| **Guide** | [below](#-deploy-on-github-actions) | [`docs/deploy-rpi.md`](docs/deploy-rpi.md) |

The GitHub Actions cron is the more reliable option (no SD-card wear, no home
power/network dependency). The Pi proves the system carries no hidden cloud
dependency — the identical fetch → filter → tailor → notify chain runs on a
15-year-old single-board computer behind home NAT.

## What it does

```
                    ┌─────────────────────────────────────────────┐
   GitHub Actions   │  fetch  ─▶  dedupe  ─▶  LLM filter  ─▶ tailor │
   or a Raspberry Pi│  ~20      seen_jobs    criteria.md     résumé │
   daily            │  sources  .json        (Gemini/Qwen)   (LaTeX)│
                    └─────────────────────────────────────────────┬─┘
                                                                   ▼
                                                    Telegram: match + tailored
                                                    PDF/.tex, with reasoning
```

1. **Fetch** — pulls listings concurrently from ~20 sources (Remotive, RemoteOK,
   Jobicy, Arbeitnow, The Muse, Himalayas, We Work Remotely, Arc, Working
   Nomads, SwissDevJobs, Relocate.me, JobSpy, LinkedIn, and a Playwright-driven
   Cloudflare-fronted Israeli board, among others).
2. **Deduplicate** — `seen_jobs.json` tracks everything already processed so each
   role is only ever evaluated and notified once.
3. **Filter** — an LLM scores each new role against [`criteria.md`](criteria.md)
   (stack fit, seniority, remote/relocation, industry exclusions, timezone) and
   explains its verdict.
4. **Tailor** — for every match, the model rewrites my base LaTeX résumé to
   emphasize the relevant experience, then compiles it to PDF.
5. **Notify** — the match, the reasoning, and the tailored CV land in Telegram.

## The design choice that makes both work

Running an "LLM app" on an ARMv6 Pi is normally a non-starter — the SDKs pull in
`grpcio`, `pydantic-core` (Rust), and native TLS stacks that have no ARMv6 wheel.
So the core has **none of them**:

- **LLM calls (Gemini/Qwen) and Telegram delivery are raw `urllib` HTTPS
  requests** — no `google-generativeai`, no `requests`. LaTeX is a `subprocess`
  call to `xelatex`. The entire fetch → filter → tailor → notify path needs
  **zero pip installs**.
- **Optional sources are lazily imported.** JobSpy (`python-jobspy`) and the
  Chromium/Playwright source are imported *inside* `fetch()`, so when their
  dependencies are absent the registry silently drops just those sources and
  everything else runs. On a Pi 1 you get ~16 of ~20 sources plus the entire
  filter/tailor/notify path; on cloud or a newer Pi you get all of them.
- **No Python 3.10+ syntax**, so whatever ships with Raspberry Pi OS
  (3.9 Bullseye / 3.11 Bookworm) or GitHub's 3.12 runner all work unchanged.

## Engineering highlights

- **Self-healing LaTeX compilation** — if `xelatex` fails, the compiler log is
  fed back to the LLM to repair the source and recompile, so a malformed CV
  never blocks a notification.
- **Model fallback** — Gemini is primary, with an optional Qwen fallback so a
  single provider outage doesn't stop the run.
- **Concurrency-safe state** — the daily job commits updated `seen_jobs.json`
  back to the repo; on a push race it rebuilds the file as a *set union* of the
  local and remote keys rather than a textual rebase, which would corrupt the
  JSON array.
- **Bounded fetch stage** — a wall-clock budget (`SCRAPE_BUDGET_SECONDS`) caps
  scraping so one throttled source (LinkedIn loves to rate-limit) can't hang the
  whole run.
- **One choke-point for every run** — the daily timer and the Telegram bot both
  execute a single `flock`'d wrapper, so the Pi's lone core never runs two
  pipelines at once; a colliding trigger is refused, not queued.
- **Secrets never touch the repo or the LLM** — API tokens come from GitHub
  Actions secrets (or a mode-600 `.env` on the Pi); the phone number on the CV is
  injected from a `CV_PHONE` secret only at compile time (see [Privacy](#privacy)).
- **Pluggable sources** — every board is a small `BaseSource` subclass behind a
  `@register` decorator, so adding a provider is one class.

## ☁️ Deploy on GitHub Actions

The [`Daily Job Search`](.github/workflows/job_search.yml) workflow runs daily
(11:00 UTC / 14:00 Israel) and on manual dispatch. Fork the repo, then add these
**Actions secrets**:

| Secret | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | LLM filtering & tailoring |
| `TELEGRAM_BOT_TOKEN` | ✅ | delivery |
| `TELEGRAM_CHAT_ID` | ✅ | delivery |
| `QWEN_API_KEY` | optional | fallback model |
| `CV_PHONE` | optional | phone injected into the CV at build time |

The workflow keeps dedup state on an orphan **`state`** branch (see [layout](#repository-layout)),
installs a right-sized XeLaTeX + Chromium, runs the pipeline, and commits the
updated `seen_jobs.json` back to `state`. No server to operate.

## 🍓 Deploy on a Raspberry Pi 1

One script provisions everything — packages, swap, timezone, a `.env` template,
seeded dedup state, a pre-warmed XeLaTeX cache, and the systemd service + timer +
control bot:

```bash
git clone https://github.com/dsx87/job-search.git ~/job-search
cd ~/job-search
bash scripts/setup-rpi.sh          # idempotent; safe to re-run
nano .env                          # fill in GEMINI_API_KEY, TELEGRAM_* (mode 600)
sudo systemctl enable --now job-search.timer job-search-bot.service
```

The full walkthrough — hardware notes, run-time expectations on a 700 MHz core,
seeding the dedup state to avoid the multi-hour first run, and troubleshooting —
is in **[`docs/deploy-rpi.md`](docs/deploy-rpi.md)**.

### Telegram control bot

The home network has no dedicated IP, so instead of a webhook the bot
**long-polls** Telegram's `getUpdates` — all outbound HTTPS, working behind NAT
with nothing to open on the router. From the authorized chat only:

| Command | What it does |
|---|---|
| `/run` | Kick off a full pipeline run now; replies with the duration or the error. |
| `/status` | In-progress vs idle, the last run's trigger / exit code / timestamps, uptime. |
| `/tailor <url>` | Tailor a CV against a job URL (auto-fetched) and send the PDF. |
| `/tailor <pasted text>` | Same, from a pasted description (fallback for login-walled URLs). |

## Run it locally (CLI / TUI)

Beyond the automated pipeline, the scraper is usable by hand — the core needs no
dependencies:

```bash
python -m job_search --json                              # scrape, print JSON
python -m job_search --sources remotive,remoteok --max-age 7
python -m job_search --list-sources
python -m job_search.tui                                 # curses TUI: browse / mark seen
```

Run the full pipeline or tailor a single CV directly (needs the env vars above +
a TeX install for the PDF):

```bash
python3 -m job_search.pipeline                           # the daily pipeline
python3 -m job_search.pipeline --tailor --url "https://…"          # auto-fetch a posting
python3 -m job_search.pipeline --tailor --job-text "$(pbpaste)" \
  --title "Senior iOS Developer" --company "Acme"                  # paste fallback
```

## Tech stack

Python (stdlib-only core) · GitHub Actions · Raspberry Pi / systemd · Playwright ·
Google Gemini / Qwen · XeLaTeX · Telegram Bot API ·
[python-jobspy](https://github.com/cullenwatson/JobSpy)

## Repository layout

| Path | Purpose |
|------|---------|
| `job_search/` | The application package (sources, filters, LLM, LaTeX, pipeline, CLIs) |
| `job_search/sources/` | ~20 pluggable job-board sources behind a `@register` registry |
| `job_search/pipeline/` | Orchestrates fetch → dedupe → filter → tailor → notify |
| `job_search/bot/` | The Telegram control bot (`/run`, `/status`, `/tailor`) |
| `job_search/latex/` | Base-CV render, `xelatex` compile, one-page guard |
| `job_search/llm/` | Gemini/Qwen clients, criteria evaluation, résumé tailoring |
| `scripts/setup-rpi.sh` | One-shot Raspberry Pi provisioning |
| `scripts/run_pipeline.sh` | The single `flock`'d entry point every run goes through |
| `tests/` | Offline characterization suite (`pytest`) |
| `criteria.md` | Human-readable job-fit rules the LLM filters against |
| `cv_tailoring_prompt.md` | Master profile + instructions for résumé tailoring |
| `igor_pivnyk_cv_base_updated.tex` | Base résumé the LLM tailors per role |
| `.github/workflows/` | Daily cron + manual CV-render + on-demand tailor workflows |

> Dedup state (`seen_jobs.json`) is **not** on `main` — the GitHub Actions run
> reads it from and commits it back to an orphan **`state`** branch, so the bot's
> bookkeeping never clutters the project history. On the Pi it simply lives in the
> working directory across runs.

## Privacy

This repo is public, so it carries **no** secrets and no personal phone number.
The résumé's phone is a `((PHONE))` placeholder that is replaced at compile time
from the `CV_PHONE` secret — it is never committed and never sent to the LLM. The
committed sample PDF is rendered with the placeholder empty.

## License

[MIT](LICENSE)
