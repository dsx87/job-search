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
   daily            │  sources  .json     (primary+fallback) (LaTeX)│
                    └─────────────────────────────────────────────┬─┘
                                                                   ▼
                                                    Telegram: match + tailored
                                                    verified one-page PDF
                                                    with reasoning
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
4. **Tailor** — for every match and every job flagged for review, the model
   rewrites my base LaTeX résumé to emphasize the relevant experience. A
   factual-content guard validates it, pdflatex compiles it, and the page guard
   verifies exactly one page.
5. **Notify** — each run is bundled into **one ZIP digest** delivered to Telegram:
   a self-contained HTML dashboard (a table of every match with a one-line
   summary, the fit reasoning, key facts, and a local link to its tailored CV,
   plus the jobs flagged for review and the deferred ones) alongside the CV PDFs
   themselves. Set `TELEGRAPH_ACCESS_TOKEN` to publish the dashboard as a
   [telegra.ph](https://telegra.ph) page instead: one AES-256 protected archive
   of ordinary PDFs is uploaded to a file host and linked from the page, so the
   whole run arrives as **one** Telegram message carrying the link and archive
   password. Set
   `DIGEST_DELIVERY=0` to fall back to the legacy per-job message + attachment
   stream.

## The design choice that makes both work

Running an "LLM app" on an ARMv6 Pi is normally a non-starter — the SDKs pull in
`grpcio`, `pydantic-core` (Rust), and native TLS stacks that have no ARMv6 wheel.
So the core has **none of them**:

- **Every LLM call is a raw `urllib` HTTPS request**, and so is Telegram delivery
  — no `google-generativeai`, no `anthropic`, no `openai`, no `requests`. Each
  provider is a small wire-protocol *scheme* (`gemini`/`openai`/`anthropic`), so
  the SDKs never enter the tree. LaTeX is a `subprocess` call to `pdflatex`. The
  fetch → filter → tailor → private-Telegram path needs **zero pip installs**;
  only optional Telegraph archive hosting adds the small `pyzipper` dependency.
- **Optional sources are lazily imported.** JobSpy (`python-jobspy`) and the
  Chromium/Playwright source are imported *inside* `fetch()`, so when their
  dependencies are absent the registry silently drops just those sources and
  everything else runs. On a Pi 1 you get ~16 of ~20 sources plus the entire
  filter/tailor/notify path; on cloud or a newer Pi you get all of them.
- **No Python 3.10+ syntax**, so whatever ships with Raspberry Pi OS
  (3.9 Bullseye / 3.11 Bookworm) or GitHub's 3.12 runner all work unchanged.

## Engineering highlights

- **Strict, self-healing CV delivery** — factual validation and repair run before
  compilation; repairable pdflatex errors are fed back to the LLM. A persistent
  content violation, compiler failure, unverifiable page count, multi-page PDF,
  or failed document upload blocks completion and is counted in the daily run
  summary. Automated delivery makes at most three attempts (days 0, 1, and 3),
  then blocks until a manual `/tailor`. Raw `.tex` is never delivered.
- **Scheme-based providers with a hardened breaker** — a provider is a
  wire-protocol scheme (`gemini`, `openai`, `anthropic`) + model + key; switching
  providers is config only, no code. A primary serves the run; an optional
  fallback covers outages. Transient 429/503s are retried, and the primary is
  disabled for the run only after repeated failures — so one blip doesn't dump
  the whole run onto the fallback.
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
- **Configured by environment, not by code** — every realistic knob (output
  mode, prompts, candidate identity, sources, LaTeX engine, ...) is an
  environment variable read once at startup. One optional trusted
  `job_search_config.py` survives as a deliberately unvalidated escape hatch
  for the rare thing that genuinely needs code, such as a pre-LLM candidate
  filter.

## Customize the runtime

Start with `python -m job_search.pipeline --check-config` to see every
effective setting and validate your environment. Most deployments only need
environment variables — see [`docs/configuration.md`](docs/configuration.md)
for the full settings table.

For the rare thing that genuinely needs code, copy the tested example and
validate it before running:

```bash
cp job_search_config.example.py job_search_config.py
python -m job_search.pipeline --check-config
```

The default file is optional, so existing users need no migration. Set
`JOB_SEARCH_CONFIG_FILE` for another path. The module is trusted executable
code, deliberately unvalidated — a mistake in it surfaces as that file's own
traceback — and must not contain secrets; continue to provide credentials and
private CV placeholders through the environment.

[`docs/configuration.md`](docs/configuration.md) documents every setting plus
`--check-config`, output modes, custom prompts, the `criteria.md` reopen
lifecycle, LM Studio local inference, and the escape hatch.

## Claude Code skills

The repo ships its own [Claude Code](https://claude.com/claude-code) skills in
[`.claude/skills/job-searcher/`](.claude/skills/job-searcher/), so an agent
working in a fresh clone already knows how this system is wired:

| Skill | Purpose |
|---|---|
| `/job-searcher:configure` | environment variables, LLM providers, `criteria.md`, source selection, digest sections, and the trusted `job_search_config.py` escape hatch |
| `/job-searcher:deploy` | the Actions cron and Raspberry Pi installs, dedup-state seeding and sync, operating a host, and run triage |
| `/job-searcher:explain` | how the pipeline works — stages, sources, provider fallback and circuit breaker, CV guards, delivery, and the local CLI/TUI |

They are a checked-in *skills-directory plugin* (`.claude-plugin/plugin.json`
next to a `skills/` directory), so they load as `job-searcher@skills-dir` with
no marketplace and no install step — start Claude Code at the repository root
and trust the workspace. `configure` and `explain` also load on their own when a
request matches; `deploy` is manual-only because it acts on live runners. The
skills are a map into `docs/configuration.md`, `docs/deploy-rpi.md`, and this
README rather than a second copy of them. See
[`.claude/skills/job-searcher/README.md`](.claude/skills/job-searcher/README.md).

## ☁️ Deploy on GitHub Actions

The [`Daily Job Search`](.github/workflows/job_search.yml) workflow runs daily
(11:00 UTC / 14:00 Israel) and on manual dispatch. Fork the repo, then add these
**Actions secrets**:

| Secret | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | primary LLM key (filtering & tailoring) |
| `TELEGRAM_BOT_TOKEN` | ✅ | delivery |
| `TELEGRAM_CHAT_ID` | ✅ | delivery |
| `OPENAI_API_KEY` | optional | fallback provider key |
| `CV_PHONE` | optional | phone injected into the CV at build time |
| `TELEGRAPH_ACCESS_TOKEN` | optional | publish the digest as a telegra.ph page instead of a ZIP; mint once with `python scripts/telegraph_account.py`. Setting it uploads one AES-256 protected CV archive to [x0.at](https://x0.at); its password is sent in the Telegram message. Without ZIP-encryption support, the run safely sends the Telegram ZIP instead |
| `JOB_SEARCH_CONFIG_PY` | optional | multiline trusted escape-hatch source materialized as `job_search_config.py`; deliberately unvalidated, so keep credentials in the other secrets, not in this code |

The workflow maps the `GEMINI_API_KEY` secret to `LLM_PRIMARY_API_KEY` and
`OPENAI_API_KEY` to `LLM_FALLBACK_API_KEY`. The default primary is the `gemini`
scheme at `gemini-2.5-flash`; the default fallback is the `openai` scheme at
`gpt-5.4-mini` (a **separate prepaid OpenAI API key** — ChatGPT Plus does not
include API access).

A tracked `job_search_config.py` is loaded automatically. If the
`JOB_SEARCH_CONFIG_PY` secret is present it is materialized after checkout and
takes precedence over the tracked file. The daily, manual-tailor, and
base-render workflows all use the same convention. Install imports needed by a
custom module in the workflow yourself. A GitHub-hosted runner cannot connect
to an LLM server bound only to your laptop's loopback interface.

> ⚠️ **`gemini-2.5-flash` is scheduled for shutdown on 2026-10-16.** It is kept
> as the default deliberately (2.5 proved steadier than the 3.x lineage on this
> workload), so the migration is a dated decision, not an oversight. The run log
> and the digest footer start warning 120 days out, and a retired model is
> reported once as "primary model rejected — check `LLM_PRIMARY_MODEL`" rather
> than silently costing one doomed request per job. **Configure the fallback
> key**: without `OPENAI_API_KEY` a retired primary is a total outage, not a
> degraded run. See `LLM_MODEL_SHUTDOWN_DATES` in `job_search/config.py`.

A provider is a **scheme** + model + key (+ optional base), so switching
providers is config only — no code change. Override any of these optional
**Actions repository variables** (Settings → Secrets and variables → Actions →
Variables): `LLM_PRIMARY_SCHEME`, `LLM_PRIMARY_MODEL`, `LLM_PRIMARY_API_BASE`,
`LLM_PRIMARY_AUTH_MODE`, `LLM_FALLBACK_SCHEME`, `LLM_FALLBACK_MODEL`,
`LLM_FALLBACK_API_BASE`, `LLM_FALLBACK_AUTH_MODE`. Unset or blank variables use
the application defaults. `SECTIONS_PY` is a variable too — see
[digest sections](#group-the-digest-into-your-own-sections). Worked examples
(the `openai` scheme covers any OpenAI-compatible endpoint via `api_base`):

| Provider | Scheme | `…_API_BASE` | Example model |
|---|---|---|---|
| Groq | `openai` | `https://api.groq.com/openai/v1` | e.g. `llama-3.3-70b` |
| xAI Grok | `openai` | `https://api.x.ai/v1` | `grok-4.3` |
| Anthropic | `anthropic` | *(default)* | `claude-haiku-4-5` |

To make one primary, swap the `LLM_PRIMARY_*` block (and its key) for the
`LLM_FALLBACK_*` values.

The workflow keeps dedup state on an orphan **`state`** branch (see [layout](#repository-layout)),
installs a right-sized pdflatex + Chromium, runs the pipeline, and commits the
updated `seen_jobs.json` back to `state`. No server to operate.

## 🍓 Deploy on a Raspberry Pi 1

One script provisions everything — packages, swap, timezone, a `.env` template,
seeded dedup state, a pre-warmed pdflatex cache, and the systemd service + timer +
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
python3 -m job_search.pipeline --check-config                 # validate + redact
python3 -m job_search.pipeline                           # the daily pipeline
python3 -m job_search.pipeline --tailor --url "https://…"          # auto-fetch a posting
python3 -m job_search.pipeline --tailor --job-text "$(pbpaste)" \
  --title "Senior iOS Developer" --company "Acme"                  # paste fallback
```

By default (`DIGEST_DELIVERY=1`) the daily flow bundles every fit, review, and
deferred job into a **single ZIP digest** (`job-digest-<date>.zip`: an HTML
dashboard + the tailored CV PDFs) and sends it in one Telegram message. A
successful send marks all included fits delivered; a failed send leaves them for
the same day-1/day-3 retry as before. The CLI `--tailor` and Telegram `/tailor`
commands are unaffected — they still deliver a single tailored CV directly.

With `TELEGRAPH_ACCESS_TOKEN` set, the dashboard is published as a telegra.ph
page instead and Telegram gets **one** message: the link and the CV password.
The page title carries eight random hex characters so its public URL is not
guessable, full job descriptions are left out (the posting link carries them,
and Telegraph caps page content at 64 KB), and one long-lived "Job Search
Digests" index page is rebuilt each run, listing the 200 most recent digests.

Telegraph cannot host files, so one `job-cvs-<date>.zip` is uploaded to
[x0.at](https://x0.at) and linked at the top of the page. The archive uses
WinZip AES-256 under one password per run that travels only in the private
Telegram message. The host therefore receives ciphertext, while successful
extraction produces ordinary PDFs that can be opened and forwarded without a
password. The upload happens *before* the page is published, so a failure leaves
nothing published. The link expires roughly 100 days after the run, and the
page says so.

If archive encryption or upload fails, no page is published and the run sends
the self-contained ZIP through Telegram exactly as before. That fallback ZIP is
not encrypted because it travels inside the private Telegram chat.

To see what the pages look like without waiting for a real run: by this point
`TELEGRAPH_ACCESS_TOKEN` is already set, and the tool refuses to publish there
on purpose, so mint a separate preview account once and reuse it:

```bash
python scripts/telegraph_preview.py --days 3 --force   # mints a preview account, prints TELEGRAPH_PREVIEW_TOKEN=...
export TELEGRAPH_PREVIEW_TOKEN=...                      # paste what the run above printed
python scripts/telegraph_preview.py --days 3            # reuses it; the index now lists 6 pages
```

Mock digests come from `job_search/digest/fixtures.py` — the same fixtures the
tests assert on. `--force` publishes to a preview account, never your
production one — unless you deliberately set `TELEGRAPH_PREVIEW_TOKEN` equal to
`TELEGRAPH_ACCESS_TOKEN` yourself.

Add `--upload` to make the archive link live. The password is printed to the
terminal; download the ZIP, confirm a wrong password fails, then extract it and
open the ordinary `igor_pivnyk_cv_<company>.pdf` files.

```bash
python scripts/telegraph_preview.py --days 1 --upload
```

The daily flow, CLI `--tailor`, and Telegram `/tailor` command share the same
delivery contract: validation, successful compilation, exactly-one-page
verification, and PDF upload must all succeed. If preparation fails after a fit
is found, Telegram receives one “verified CV pending” notice with the next retry
date. Successful text delivery is persisted immediately, so a later document
retry uploads only the newly verified PDF and never repeats the fit message.
Failures retry on days 1 and 3; after the third failed attempt, automated work
stops and Telegram directs recovery through `/tailor`. Manual tailoring bypasses
the daily retry state.

## Group the digest into your own sections

By default the digest lists every fit in one stack. Copy `sections.example.py`
to `sections.py` and it groups them under headings you define — Israel roles,
worldwide-remote roles, EU relocation, whatever you want:

```python
from job_search.digest.sections import Section, all_of, fact, is_remote, on_job
from job_search.location.classify import is_israel_job

SECTIONS = [
    Section("Israel", "🇮🇱", applies_to=("fits", "review"),
            match=on_job(is_israel_job)),
    Section("Remote — Worldwide", "🌍",
            match=all_of(is_remote, fact("remote_geo_scope", "worldwide"))),
    Section("Everything else", "📋"),      # no match = catch-all
]
```

The config is Python rather than YAML or JSON on purpose: a section can call
anything in the repo, so `on_job(is_israel_job)` reuses the real location
database instead of re-listing city names in a rule language that would have to
grow an operator every time you wanted a new kind of rule.

- **Order is priority.** Each job appears exactly once, under the first section
  it matches. Anything left over goes to an automatic "Other".
- **`applies_to`** picks the lists a section groups — `"fits"` (the default) and
  `"review"`. Deferred jobs were never evaluated, so they stay a flat list.
- **Sections are presentation only.** They change nothing about what is
  scraped, filtered, evaluated, or delivered; the ZIP still holds one tailored
  CV per fit.
- **A broken config never costs you a run.** The digest is delivered ungrouped,
  a warning strip at the top of the dashboard says what was wrong, and Telegram
  alerts you. A typo like `e.job.is_remot` is caught while the file is loaded,
  not mid-render.
- **`sections.py` stays untracked.** It's per-host local config — only
  `sections.example.py` is in git, so `git add sections.py` is refused without
  `-f`. On a host deployed by `git pull` (the Raspberry Pi), create it directly
  on the host.
- **On GitHub Actions, use the `SECTIONS_PY` repository variable.** A runner
  only ever sees what is committed, so an untracked `sections.py` is never
  there. Paste the file's *contents* into the variable (Settings → Secrets and
  variables → Actions → Variables) and the workflow writes it out before the
  run. Leave it unset and CI simply doesn't group. The Actions log prints the
  section names it parsed, so a typo shows up there as a warning annotation
  rather than only as a Telegram alert.
- **`SECTIONS_FILE`** overrides the path the config is read from, so a host can
  point somewhere else entirely.

### Helper vocabulary

Everything below lives in `job_search.digest.sections`. A raw
`lambda entry: ...` works anywhere a `match=` is expected — these just keep the
common case short.

| Helper | What it does |
|--------|---------------|
| `all_of(*predicates)` | Matches when every predicate matches (AND). |
| `any_of(*predicates)` | Matches when any predicate matches (OR). |
| `not_(predicate)` | Inverts a predicate. |
| `is_remote` | Matches a remote job. |
| `in_region(*regions)` | Matches when the job's region is one of `regions` (e.g. `Region.EU`). |
| `fact(name, *values)` | With values, matches when the LLM-extracted fact equals one of them (case-insensitive). With none, matches when the fact is known at all — present and not the "unknown" the extractor writes when a posting doesn't state it. |
| `location_contains(*tokens)` | Matches when the job's location contains any token, case-insensitive. |
| `title_matches(pattern)` | Matches the job title against a case-insensitive regex, compiled once at section-definition time so a broken pattern surfaces at load, not mid-render. |
| `on_job(fn)` | Adapts a job-taking function into an entry-taking predicate — the bridge to anything already written in this repo, e.g. `on_job(is_israel_job)`. |
| `days_since_posted(entry)` | **Not a predicate** — returns the posting's age in days as an `int`, or `None` when the source gave no date. Use it inside a lambda, e.g. `match=lambda e: days_since_posted(e) is not None and days_since_posted(e) <= 3`. Test against `None` explicitly rather than with `or` — a job posted today is `0`, which `or` would treat as missing. |

## Tech stack

Python (stdlib-only core) · GitHub Actions · Raspberry Pi / systemd · Playwright ·
scheme-based LLM providers (Gemini / OpenAI-compatible / Anthropic) · pdflatex ·
Telegram Bot API · [python-jobspy](https://github.com/cullenwatson/JobSpy)

## Repository layout

| Path | Purpose |
|------|---------|
| `job_search/` | The application package (sources, filters, LLM, LaTeX, pipeline, CLIs) |
| `job_search/sources/` | ~20 pluggable job-board sources behind a `@register` registry |
| `job_search/pipeline/` | Orchestrates fetch → dedupe → filter → tailor → notify |
| `job_search/bot/` | The Telegram control bot (`/run`, `/status`, `/tailor`) |
| `job_search/latex/` | Base-CV render, `pdflatex` compile, one-page guard |
| `job_search/llm/` | scheme-based LLM providers, criteria evaluation, résumé tailoring |
| `job_search/components.py` | the concrete object graph: profile, prompts, CV rendering, output delivery |
| `job_search/runtime.py` | builds the `Runtime` from settings, applies the escape hatch, preflights the host |
| `scripts/setup-rpi.sh` | One-shot Raspberry Pi provisioning |
| `scripts/run_pipeline.sh` | The single `flock`'d entry point every run goes through |
| `tests/` | Offline characterization suite (`pytest`) |
| `criteria.md` | Human-readable rules and built-in evaluation fingerprint input; executable defaults live in `job_search/policy.py` |
| `cv_tailoring_prompt.md` | Compatibility artifact; deterministic bullet selection no longer consumes its instruction block |
| `job_search_config.example.py` | no-op template for the `job_search_config.py` escape hatch |
| `sections.example.py` | Example digest sections — copy to `sections.py` to group the dashboard |
| `igor_pivnyk_cv_base_updated.tex` | Base résumé the LLM tailors per role |
| `.github/workflows/` | Daily cron + manual CV-render + on-demand tailor workflows |
| `.claude/skills/job-searcher/` | Checked-in Claude Code skills: `/job-searcher:configure`, `:deploy`, `:explain` |

> Dedup state (`seen_jobs.json`) is **not** on `main` — the GitHub Actions run
> reads it from and commits it back to an orphan **`state`** branch, so the bot's
> bookkeeping never clutters the project history. On the Pi it simply lives in the
> working directory across runs.

## Privacy

This repo is public, so it carries **no** secrets and no personal phone number.
The résumé's phone is a `((PHONE))` placeholder that is replaced at compile time
from the `CV_PHONE` secret — it is never committed and never sent to the LLM. The
committed sample PDF is rendered with the placeholder empty.

The telegra.ph delivery path puts two things where a URL is the only thing
guarding them, and both are handled deliberately:

- **The digest page.** Public to anyone who has the link; the eight random hex
  characters in its title are what make the link unguessable. It carries no
  phone number, no full job descriptions and — by construction, not by
  convention — no CV password: the password is never put on the object the
  renderer sees.
- **The hosted CV archive.** Uploaded to x0.at under a 24-character random id
  after AES-256 protection, so the host and anyone who stumbles on the link
  holds ciphertext. One password per run is delivered over Telegram only. A
  leaked password exposes that run's CVs and no earlier one.

The practical cost is one extraction step. WinZip AES archives require an
AES-capable extractor such as 7-Zip or Keka; after extraction, the PDFs
themselves are unencrypted.

## License

[MIT](LICENSE)
