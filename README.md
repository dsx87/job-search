# AI Job Hunter

An autonomous, self-hosted job-search agent. Every morning it scrapes ~20 job
boards, filters the results against my personal criteria with an LLM, tailors my
résumé to each matching role, compiles it to PDF, and delivers the matches —
with custom CVs attached — to Telegram. It runs entirely on a free GitHub
Actions cron; there is no server to operate.

> Built to run my own job search end-to-end. It's a working system, not a demo.

## What it does

```
                    ┌─────────────────────────────────────────────┐
   GitHub Actions   │  fetch  ─▶  dedupe  ─▶  LLM filter  ─▶ tailor │
   daily @ 07:00 UTC│  ~20      seen_jobs    criteria.md     résumé │
                    │  sources  .json        (Gemini/Qwen)   (LaTeX)│
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
- **Secrets never touch the repo or the LLM** — API tokens come from GitHub
  Actions secrets; the phone number on the CV is injected from a `CV_PHONE`
  secret only at compile time (see [Privacy](#privacy)).
- **Pluggable sources** — every board is a small `BaseSource` subclass, so
  adding a provider is one class.

## Tech stack

Python 3.12 · GitHub Actions · Playwright · Google Gemini / Qwen · XeLaTeX ·
Telegram Bot API · [python-jobspy](https://github.com/cullenwatson/JobSpy)

## Repository layout

| Path | Purpose |
|------|---------|
| `job_search/` | The application package (sources, filters, LLM, LaTeX, pipeline, CLIs) |
| `job_search/sources/` | ~20 pluggable job-board sources behind a `@register` registry |
| `job_search/pipeline/` | Orchestrates fetch → dedupe → filter → tailor → notify |
| `job_search/latex/render_base.py` | Renders the base CV to PDF (used by CI) |
| `tests/` | Offline characterization suite (`pytest`) |
| `criteria.md` | Human-readable job-fit rules the LLM filters against |
| `cv_tailoring_prompt.md` | Master profile + instructions for résumé tailoring |
| `igor_pivnyk_cv_base_updated.tex` | Base résumé the LLM tailors per role |
| `.github/workflows/` | Daily cron + manual CV-render + on-demand tailor workflows |

> Dedup state (`seen_jobs.json`) is **not** on `main` — the daily run reads it
> from and commits it back to an orphan **`state`** branch, so the bot's
> bookkeeping never clutters the project history.

## Running it

The pipeline is driven by environment variables (provided as repo secrets in CI):

```bash
export GEMINI_API_KEY=...       # required — LLM filtering & tailoring
export TELEGRAM_BOT_TOKEN=...   # required — delivery
export TELEGRAM_CHAT_ID=...     # required — delivery
export QWEN_API_KEY=...         # optional — fallback model
export CV_PHONE=...             # optional — phone injected into the CV at build time

pip install -r requirements.txt
python -m playwright install chromium   # for the Playwright-backed source
python3 -m job_search.pipeline          # the daily pipeline
python3 -m job_search                    # the scraper CLI (interactive menu)
```

### Tailor a CV on demand

Besides the daily run, the **Tailor CV** workflow (Actions tab → *Run workflow*,
or `.github/workflows/tailor_cv.yml`) tailors a résumé for one specific job and
sends the PDF to Telegram. Give it a job **URL** (auto-fetched) and/or paste the
**job text**; if the URL is blocked or JavaScript-rendered the run fails with a
clear message and you re-run with the text pasted. Same engine as the pipeline —
Gemini/Qwen tailoring, xelatex compile, `CV_PHONE` injected at build time.

```bash
# Locally (needs the same env vars as above + a TeX install for the PDF):
python3 -m job_search.pipeline --tailor --url "https://…"            # auto-fetch
python3 -m job_search.pipeline --tailor --job-text "$(pbpaste)" \
  --title "Senior iOS Developer" --company "Acme"                     # paste fallback
```

## Privacy

This repo is public, so it carries **no** secrets and no personal phone number.
The résumé's phone is a `((PHONE))` placeholder that is replaced at compile time
from the `CV_PHONE` secret — it is never committed and never sent to the LLM. The
committed sample PDF is rendered with the placeholder empty.

## License

[MIT](LICENSE)
