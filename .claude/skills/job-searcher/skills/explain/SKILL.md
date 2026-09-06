---
name: explain
description: Explain and drive the AI Job Hunter's functionality — the fetch/dedupe/filter/tailor/notify stages, the ~20 pluggable sources, LLM provider fallback and circuit breaker, CV tailoring guards, digest and telegra.ph delivery, retry state, and the local CLI/TUI commands.
when_to_use: Triggered by questions like "how does the job search decide a match", "what happens when the LLM fails", "why is this job not in the digest", "what does the one-page guard do", "how do I tailor a CV by hand", "where does seen_jobs.json live", "add a new job board".
argument-hint: [topic or question]
allowed-tools: Read, Grep, Glob, Bash(python3 -m job_search*), Bash(python -m job_search*), Bash(python3 -m pytest*)
---

# How the job searcher works

Answer from the code, not from this summary. This file is a map: it tells you
which module owns the answer so you can read it before replying.

## The daily chain

`python3 -m job_search.pipeline` → `job_search/pipeline/run.py`, stages in
`job_search/pipeline/stages.py`, CLI in `job_search/pipeline/cli.py`.

1. **Fetch** — ~20 sources concurrently (`job_search/sources/fetch.py`). The
   whole stage is capped by a wall-clock budget (`SCRAPE_BUDGET_SECONDS`,
   default 600) so one throttled source cannot hang the run. Responses are
   capped at 8 MB (`MAX_RESPONSE_BYTES`) — an unbounded read is an OOM on a
   512 MB Pi.
2. **Deduplicate** — `seen_jobs.json` via `job_search/state/job_store.py`. Every
   role is evaluated and notified at most once.
3. **Filter** — the LLM scores each new role against `criteria.md`; executable
   defaults are in `job_search/policy.py`, evaluation in `job_search/llm/`.
   Outcomes: fit, review, deferred, rejected.
4. **Tailor** — for each fit and each review, the model selects bullets from the
   base LaTeX résumé; a factual-content guard validates, `pdflatex` compiles,
   and a page guard verifies exactly one page (`job_search/latex/`).
5. **Notify** — `job_search/digest/` + `job_search/notify/`.

## Design constraints worth knowing before answering

- **Stdlib-only core.** Every LLM call and every Telegram call is a raw `urllib`
  HTTPS request (`job_search/http.py`); LaTeX is a `subprocess`. No SDKs, no
  `requests`, no Rust wheels — that is what lets it run on ARMv6. Only the
  Telegraph archive path adds `pyzipper`.
- **Optional sources are lazily imported** inside `fetch()`, so a missing
  `python-jobspy` or `playwright` silently drops just those sources.
- **No Python 3.10+ syntax**, so 3.9 through 3.12 all work. Keep it that way in
  any edit.

## Providers and failure behavior

`job_search/llm/clients.py`. A provider is a wire-protocol **scheme**
(`gemini` | `openai` | `anthropic`) + model + key (+ optional base). A primary
serves the run; an optional fallback covers outages.

- 429/500/502/503/504 → retried internally with backoff `(2, 8, 20)`.
- 429/503 post-retry → increments the consecutive-failure counter; the primary is
  disabled for the run only at `LLM_BREAKER_THRESHOLD` (default 2), so one blip
  does not dump the run onto the fallback.
- 404 → the model does not exist here (retired or misspelled). Not retryable;
  the primary is disabled after one loud message rather than paying one doomed
  request per job.
- 400 → this *request* was rejected. Deliberately not treated as a model
  rejection, because Gemini returns `INVALID_ARGUMENT` for per-request
  conditions and this pipeline feeds it arbitrary scraped text. Falls back for
  that request only, called out once.

`LLM_MODEL_SHUTDOWN_DATES` in `job_search/config.py` drives the shutdown warning
in the run log and digest footer, starting 120 days out.

## Delivery

Default (`DIGEST_DELIVERY=1`): one ZIP per run — a self-contained HTML dashboard
(every fit with a one-line summary, fit reasoning, key facts, and a local link to
its CV, plus reviews and deferred jobs) alongside the tailored PDFs, in one
Telegram message. `DIGEST_DELIVERY=0` reverts to the legacy per-job stream.

With `TELEGRAPH_ACCESS_TOKEN` set: the dashboard is published as a telegra.ph
page and Telegram gets **one** message — the link and the archive password. The
page title carries eight random hex characters so the URL is unguessable, full
descriptions are left out (Telegraph caps content at 64 KB), and a long-lived
index page listing the 200 most recent digests is rebuilt each run. Telegraph
cannot host files, so one WinZip AES-256 archive of ordinary PDFs goes to x0.at
under a 24-character id and is linked from the page; the host holds ciphertext,
the link expires in ~100 days, and there is no delete API. The upload happens
*before* publication, so a failure leaves nothing published and the run sends the
plain Telegram ZIP instead.

**The delivery contract is the same** for the daily flow, CLI `--tailor`, and the
Telegram `/tailor`: validation, successful compilation, verified one page, and
PDF upload must all succeed. Raw `.tex` is never delivered. A failure after a fit
is found sends one "verified CV pending" notice with the next retry date; text
delivery is persisted immediately so a later retry uploads only the new PDF and
never repeats the fit message. Automated attempts run on days 0, 1, and 3; after
the third, work stops and Telegram directs recovery through `/tailor`.

## Sources

`job_search/sources/` — every board is a small `BaseSource` subclass behind a
`@register` decorator, grouped by transport (`api_sources.py`, `rss_sources.py`,
`html_sources.py`, `jobspy_sources.py`, `playwright_sources.py`,
`linkedin_guest.py`). Adding a board is one class.

```bash
python3 -m job_search --list-sources      # names, with (default: off) marked
```

`linkedin-guest` is a stdlib-only LinkedIn guest-API source, default-off, and is
what the ARMv6 Pi runs instead of the jobspy-backed LinkedIn sources.

## Running it by hand

```bash
python3 -m job_search --json                                  # scrape, print JSON
python3 -m job_search --sources remotive,remoteok --max-age 7
python3 -m job_search --list-sources
python3 -m job_search.tui                                     # curses browser

python3 -m job_search.pipeline --check-config                 # validate + redact
python3 -m job_search.pipeline --list                         # new jobs, no LLM, no writes
python3 -m job_search.pipeline --test                         # force one job end-to-end, no writes
python3 -m job_search.pipeline --seed                         # mark everything fetched as seen
python3 -m job_search.pipeline                                # the daily run
python3 -m job_search.pipeline --tailor --url "https://…"
python3 -m job_search.pipeline --tailor --job-text "$(pbpaste)" \
  --title "Senior iOS Developer" --company "Acme"
```

`--list`, `--test`, `--check-config`, and `--tailor` do not touch
`seen_jobs.json`. `--seed` resets the dedup baseline. Preview the Telegraph
pages without a real run using `scripts/telegraph_preview.py` (mints a separate
preview account; `--upload` makes the archive link live).

## Privacy facts to state accurately

The repo is public and carries no secrets. The résumé's phone is a `((PHONE))`
placeholder replaced at compile time from `CV_PHONE` — never committed, never
sent to the LLM. The digest page carries no phone number and, by construction,
no CV password: the password is never placed on the object the renderer sees.

## Answering method

1. Locate the owning module with `Grep`/`Glob` and read it.
2. Quote the real default from `job_search/config.py`, not from memory — the
   comments there record *why* each value is what it is.
3. For a "why did it do X" question, prefer the run log, `.last_run.json`, or
   `logs/run-*.log` over inference.
4. `python3 -m pytest -q` is the offline characterization suite: no network, safe
   to run to check a claim.
5. Related skills: `/job-searcher:configure` to change behavior,
   `/job-searcher:deploy` to install or operate a runner.
