---
name: configure
description: Configure the AI Job Hunter — environment variables, LLM providers, search criteria, source selection, digest sections, and the optional job_search_config.py escape hatch. Use when changing what the pipeline searches for, which model it calls, how the digest is grouped, or where results are delivered.
when_to_use: Triggered by requests like "switch the job search to Groq", "why is it rejecting these roles", "add LinkedIn to the sources", "group the digest by region", "write the digest to disk instead of Telegram", "check my job-search config".
argument-hint: [what to change]
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python3 -m job_search*), Bash(python -m job_search*)
---

# Configure the job searcher

Four layers, cheapest first. Change the highest layer that does the job.

| Layer | File / mechanism | Use for |
|---|---|---|
| 1. Environment | `.env`, Actions secrets & variables | credentials, provider choice, tuning, on/off switches |
| 2. Search intent | `criteria.md` | what counts as a match |
| 3. Presentation | `sections.py` | how the digest dashboard is grouped |
| 4. Escape hatch | `job_search_config.py` | the rare thing with no setting — unvalidated, rare |

**Check the settings table first.** Almost everything that once needed layer 4
is now a plain environment variable: the LaTeX engine (`LATEX_ENGINE`), where
results go (`OUTPUT_MODE` / `OUTPUT_DIR` / `OUTPUT_CV_MODE`), prompt overrides
(`PROMPT_DIR` / `PROMPT_REVISION`), and the candidate's identity
(`CV_DISPLAY_NAME` / `CV_FILENAME_PREFIX`). Only reach for layer 4 when no
setting exists.

## Always finish with the checker

```bash
python3 -m job_search.pipeline --check-config
```

It builds the runtime, runs preflight, prints effective settings plus a
`runtime` block (collaborator class names, the derived flags, and `config_file`
— the escape-hatch path that ran, or `null`) as JSON, and redacts keys, tokens,
and chat ids. It does
not scrape, call an LLM, compile, mutate state, or deliver. Run it after any
change on this layer, and treat a non-zero exit as the change being rejected.

## Layer 1 — environment

Read `reference/env-vars.md` in this plugin (`${CLAUDE_PLUGIN_ROOT}/reference/env-vars.md`)
for the full table: credentials, the eight `LLM_*` provider knobs, file paths,
source selection, worker counts, and the GitHub Actions secret/variable names.

Rules that catch people out:

- A provider is **scheme + model + key (+ base)**. Switching providers is a
  config edit; never a code change. The `openai` scheme covers any
  OpenAI-compatible endpoint via `*_API_BASE`.
- Blank and whitespace-only values fall back to defaults.
- Configure the **fallback** key. Without it a retired primary model turns every
  job into an evaluation failure and the run delivers nothing.
- On GitHub Actions, `LLM_*` and `SECTIONS_PY` are repository **variables**;
  keys are **secrets**. Local files that are untracked (`sections.py`,
  `job_search_config.py`) do not exist on a runner — pass their contents through
  `SECTIONS_PY` / `JOB_SEARCH_CONFIG_PY`.
- On a Pi, `.env` is mode 600 and loaded by systemd `EnvironmentFile`.

Source selection: `python3 -m job_search --list-sources` prints the names and
marks the default-off ones. `SOURCES_ENABLE` forces default-off sources on,
`SOURCES_DISABLE` forces default-on sources off; both are comma lists.

## Layer 2 — criteria.md

`criteria.md` is the human-readable rule set the LLM scores each role against,
and it feeds the built-in evaluator **fingerprint**. Executable defaults live in
`job_search/policy.py`; the document itself does not execute policy.

> Changing `criteria.md` changes the fingerprint, which **reopens previously
> rejected jobs**. The next run re-evaluates them at full LLM cost. Say so
> before editing it, and prefer one deliberate edit over several small ones.

## Layer 3 — sections.py

Groups the digest dashboard under headings. Start from `sections.example.py`:

```bash
cp sections.example.py sections.py
```

Order is priority — each job appears once, under the first section it matches;
leftovers go to an automatic "Other". `applies_to` picks which lists a section
groups (`"fits"`, `"review"`); deferred jobs stay a flat list. Sections are
presentation only and never change what is scraped, evaluated, or delivered. A
broken config never costs a run: the digest ships ungrouped with a warning strip
and a Telegram alert.

The helper vocabulary (`all_of`, `any_of`, `not_`, `is_remote`, `in_region`,
`fact`, `location_contains`, `title_matches`, `on_job`, `days_since_posted`) is
tabulated in the README section "Group the digest into your own sections", and
lives in `job_search/digest/sections.py`. `sections.py` stays untracked; on
Actions use the `SECTIONS_PY` variable.

## Layer 4 — job_search_config.py (the escape hatch)

Reach here only when no setting covers the need — a hand-written job filter, or
an LLM client the three schemes can't express. A Python module exporting one
function, mutating the runtime in place:

```python
def configure(runtime, settings):
    runtime.candidate_filter = lambda job: "qa" not in job.title.lower()
```

`runtime` is a `job_search.runtime.Runtime`; `settings` is the effective
`PipelineConfig`. Nothing subclasses anything and there is no plugin discovery:
the module imports what it needs and you install those imports yourself
(including in the workflow, for Actions).

**It is deliberately unvalidated.** A mistake surfaces as that file's own
traceback, not a tidy message. Three things do raise a clear error: an explicit
`JOB_SEARCH_CONFIG_FILE` naming a missing file, a module without `configure`,
and an old-style `configure(defaults, settings)` signature. An *empty*
`JOB_SEARCH_CONFIG_FILE` is an error too, so a blank `.env` line can't silently
disable a local config.

If the hatch swaps the backend or renderer, flip the matching derived flags
(`runtime.cv_required`, `needs_telegram`, `needs_base_tex`, `telegram_markup`)
so preflight and the pipeline stay coherent with it.

```bash
cp job_search_config.example.py job_search_config.py
python3 -m job_search.pipeline --check-config
```

**Security boundary:** this file is trusted executable code and is deliberately
trackable in git. Never put credentials, tokens, or private CV values in it —
those belong in the environment, a mode-600 `.env`, or Actions secrets. Review
it like application source before every commit.

`docs/configuration.md` is the full settings reference.

## Working method

1. Read the current state before proposing a change — `--check-config` output,
   the relevant file, and `job_search/config.py` for the actual default.
2. Make the change at the highest layer that suffices.
3. Re-run `--check-config`.
4. If the change touches evaluation or delivery, say what it costs on the next
   run (reopened jobs, re-tailoring, extra LLM calls) before it runs.
5. If tests are relevant, `python3 -m pytest -q` (offline suite, no network).
