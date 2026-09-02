# Configuration

The pipeline is configured by environment variables — `PipelineConfig.from_env()`
reads them once at startup. Every realistic knob is a setting. One optional
`job_search_config.py` survives as a deliberately *unvalidated* escape hatch for
the rare thing that genuinely needs code (see below); most deployments never
need it.

## Settings

| Group | Variable | Default | Purpose |
|---|---|---|---|
| Files | `JOB_SEARCH_CONFIG_FILE` | optional `job_search_config.py` | escape-hatch module path (see below) |
| Files | `SEEN_JOBS_FILE` | `seen_jobs.json` | persisted dedupe/retry state |
| Files | `CRITERIA_FILE` | `criteria.md` | evaluation criteria text and the default reopen-fingerprint input |
| Files | `CV_TAILORING_PROMPT_FILE` | `cv_tailoring_prompt.md` | compatibility instruction file (bullet selection is deterministic and no longer reads its prompt block) |
| Files | `BASE_TEX_FILE` | `igor_pivnyk_cv_base_updated.tex` | base CV LaTeX source |
| Files | `OUT_PDF_FILE` | `igor_pivnyk_cv_base_updated.pdf` | rendered base-CV output path |
| Files | `SECTIONS_FILE` | `sections.py` | optional digest section grouping; a missing/invalid file is a soft fallback to an ungrouped digest |
| Candidate & CV | `CV_DISPLAY_NAME` | `Igor Pivnyk` | name on the CV and in tailoring prompts |
| Candidate & CV | `CV_FILENAME_PREFIX` | `igor_pivnyk_cv` | prefix of every tailored PDF filename |
| Candidate & CV | `CV_PHONE` | unset | substituted for `((PHONE))` at compile time only; never stored, never written to the repo |
| LLM | `LLM_PRIMARY_SCHEME` | `gemini` | primary wire scheme: `gemini` \| `openai` \| `anthropic` |
| LLM | `LLM_PRIMARY_MODEL` | `gemini-2.5-flash` | primary model (also read as legacy `GEMINI_MODEL`) |
| LLM | `LLM_PRIMARY_API_KEY` | unset | primary key (also read as legacy `GEMINI_API_KEY`) |
| LLM | `LLM_PRIMARY_API_BASE` | scheme default | primary endpoint override (also read as legacy `GEMINI_API_BASE`) |
| LLM | `LLM_PRIMARY_AUTH_MODE` | `bearer` | `bearer`, or explicit `none` for a trusted local OpenAI-compatible server |
| LLM | `LLM_FALLBACK_SCHEME` | `openai` | fallback scheme; `openai` covers any OpenAI-compatible endpoint via `*_API_BASE` |
| LLM | `LLM_FALLBACK_MODEL` | `gpt-5.4-mini` | fallback model |
| LLM | `LLM_FALLBACK_API_KEY` | unset | fallback key (also read as legacy `OPENAI_API_KEY`) |
| LLM | `LLM_FALLBACK_API_BASE` | scheme default | fallback endpoint override, e.g. Groq, xAI |
| LLM | `LLM_FALLBACK_AUTH_MODE` | `bearer` | fallback `bearer` / explicit `none` |
| LLM | `LLM_BREAKER_THRESHOLD` | `2` | consecutive circuit-break failures before the primary is disabled for the run |
| LLM | `ANTHROPIC_MAX_TOKENS` | `4096` | `max_tokens` sent to the Anthropic scheme |
| LLM | `TELEGRAM_BOT_TOKEN` | unset | delivery credential |
| LLM | `TELEGRAM_CHAT_ID` | unset | delivery credential; also the only chat the control bot accepts commands from |
| LLM | `EVAL_WORKERS` | `12` | concurrent LLM evaluation calls |
| LLM | `TAILOR_WORKERS` | `8` | concurrent tailoring calls |
| Output | `OUTPUT_MODE` | `telegram` | `telegram` \| `html` \| `plain` — chooses the renderer/backend pair as a unit |
| Output | `OUTPUT_DIR` | unset | filesystem destination for `html`/`plain` modes; unused for `telegram` |
| Output | `OUTPUT_CV_MODE` | `required` | `required` \| `disabled` — disabled skips CV work entirely; `telegram` requires `required` (see [Output modes](#output-modes)) |
| Output | `DIGEST_DELIVERY` | on (`1`) | one ZIP/page per run instead of a stream of per-job Telegram messages; `0` reverts to legacy per-job delivery |
| Output | `TELEGRAPH_ACCESS_TOKEN` | unset | publish the digest as a telegra.ph page plus one AES-256 CV archive on x0.at, so the whole run is one Telegram message |
| Prompts | `PROMPT_DIR` | unset | directory of file-backed prompt overrides (`fact_extraction.txt`, `job_summary.txt`, `cv_bullet_selection.txt`, `compiler_repair.txt`) |
| Prompts | `PROMPT_REVISION` | unset | required whenever `PROMPT_DIR` is set; participates in the reopen fingerprint (see below) |
| Prompts | `LATEX_ENGINE` | `pdflatex` | LaTeX executable used to compile the CV, e.g. `xelatex` |
| Prompts | `LATEX_MAX_WORKERS` | `2` | concurrent `pdflatex` compilations (legacy name `XELATEX_MAX_WORKERS` still honored) |
| Sources & state | `SOURCES_ENABLE` | unset | comma list of sources forced on |
| Sources & state | `SOURCES_DISABLE` | unset | comma list of sources forced off |
| Sources & state | `SCRAPE_BUDGET_SECONDS` | `600` | wall-clock ceiling on the fetch stage |
| Sources & state | `STATE_SYNC` | off (`0`) | `1` git-syncs `seen_jobs.json` with the orphan `state` branch around each run |

Two combinations are rejected at startup rather than failing mid-run: **`OUTPUT_MODE=telegram` requires `OUTPUT_CV_MODE=required`**
(Telegram has no text-only delivery path), and **`PROMPT_DIR` requires
`PROMPT_REVISION`** (prompt wording feeds the reopen fingerprint below, so an
unnamed revision would silently reuse the wrong one).

## Checking your configuration

Validate configuration without scraping, changing state, calling an LLM,
compiling a CV, or delivering output:

```bash
python -m job_search.pipeline --check-config
```

It prints every setting plus a `runtime` block as JSON — collaborator class
names, the four derived flags, and `config_file` (the escape-hatch path that
ran, or `null`). API keys, tokens, and chat identifiers are redacted:

```json
"runtime": {
  "backend": "DefaultOutputBackend",
  "cv_required": true,
  "needs_telegram": true,
  "config_file": null
}
```

## Output modes

`OUTPUT_MODE` chooses the renderer/backend pair as a unit:

- **`telegram`** (default) — per-job messages, or (with `DIGEST_DELIVERY=1`,
  the default) one ZIP/telegra.ph page per run. Requires `TELEGRAM_BOT_TOKEN`
  and `TELEGRAM_CHAT_ID`, and always runs with `OUTPUT_CV_MODE=required`.
- **`html`** — a filesystem generation with an HTML digest under `OUTPUT_DIR`,
  staged hidden and promoted atomically with its artifacts.
- **`plain`** — the same generation with a plain-text digest, for scripts or
  notifications that shouldn't render markup.

For `html`/`plain`, set `OUTPUT_CV_MODE=disabled` to skip tailoring and
compilation — a successful delivery completes the fit without a CV. Manual
`--tailor` is rejected at preflight when CV work is disabled.

## Custom prompts

`PROMPT_DIR` points at a directory of `string.Template` overrides using four
conventional filenames — `fact_extraction.txt`, `job_summary.txt`,
`cv_bullet_selection.txt`, `compiler_repair.txt`. A file missing from the
directory falls back individually to the built-in prompt. Fact/summary
templates receive `$title`, `$company`, `$location`, `$is_remote`,
`$description`; CV selection also gets `$resume_bullets`/`$candidate_name`;
compiler repair gets `$tex_source`/`$compiler_errors`. Use `$$` for a literal
`$`. `PROMPT_REVISION` is required whenever `PROMPT_DIR` is set.

## criteria.md and the reopen lifecycle

`criteria.md` is plain text evaluated by the built-in policy in
`job_search/policy.py` — the document itself executes nothing. A
previously-rejected job reopens when its stored `criteria_fingerprint` no
longer matches: that changes when `criteria.md` changes, or when
`PROMPT_REVISION` changes from what was recorded. With no custom
`PROMPT_DIR`/`PROMPT_REVISION`, the fingerprint is exactly
`criteria_version(criteria)` — changing prompts alone doesn't reopen anything.

## Local OpenAI-compatible inference

No escape-hatch module is needed for a local endpoint. LM Studio is the tested
example: start its local server, load a model that supports structured output,
and set:

```dotenv
LLM_PRIMARY_SCHEME=openai
LLM_PRIMARY_MODEL=your-loaded-model-id
LLM_PRIMARY_API_BASE=http://127.0.0.1:1234/v1
LLM_PRIMARY_AUTH_MODE=none
LLM_PRIMARY_API_KEY=
```

No-auth mode omits the `Authorization` header while retaining the OpenAI
chat-completions JSON-schema request. It requires an explicit, non-default
HTTP(S) `LLM_PRIMARY_API_BASE`; a blank, malformed, or public OpenAI endpoint is
rejected, and the mode is never inferred from a loopback URL. The URL is
relative to the machine running the pipeline: a GitHub-hosted runner cannot
reach LM Studio on your laptop. See the
[LM Studio server guide](https://lmstudio.ai/docs/developer/core/server) and
[structured-output guide](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

## job_search_config.py, the escape hatch

Everything above is a setting. Reach for `job_search_config.py` only for the
rare thing that genuinely needs code — a candidate filter, say, which has no
setting of its own. **Check the settings table first; only reach for the
config file when no setting exists.**

> **This module is deliberately unvalidated.** Nothing inspects what you hand
> back. A mistake in it surfaces as that file's own traceback, unmodified —
> the same way a bug in any other module you import would.

If present, it is loaded from the working directory (or the path named by
`JOB_SEARCH_CONFIG_FILE`) and must export one function, called once after the
built-in object graph is built from settings and before preflight validates
the host:

```python
def configure(runtime, settings):
    runtime.candidate_filter = lambda job: job.is_remote and "ios" in job.title.lower()
    return runtime
```

`runtime` is a `job_search.runtime.Runtime` — `llm`, `prompts`, `cv_renderer`,
`renderer`, `backend`, `candidate_filter`, and four derived flags. Mutate it in
place, return a replacement, or both; `build_runtime` uses whatever comes
back, or the runtime unchanged if `configure` returns `None`.

`build_runtime` raises `ConfigurationError` before any pipeline side effect in
exactly three cases: an explicit `JOB_SEARCH_CONFIG_FILE` names a file that
doesn't exist; the module has no `configure`; or the module uses the old
`configure(defaults, settings)` signature (a one-line migration message
instead of an opaque `TypeError`). **An empty `JOB_SEARCH_CONFIG_FILE` is also
an error**, not "disabled" — a blank line in a deployed `.env` shouldn't
silently bypass a configured file. Anything `configure()` itself raises
propagates unmodified.

> **Security boundary:** this file is trusted executable code, run with the
> process's own environment and secrets, on nothing more than its presence in
> the working directory. `.gitignore` deliberately tracks it — review changes
> to it the way you would review a CI workflow. Never put credentials, tokens,
> or private CV values in it; those belong only in environment variables, a
> mode-600 `.env`, or GitHub Actions secrets.

Start from `job_search_config.example.py`, a no-op until you edit it:

```bash
cp job_search_config.example.py job_search_config.py
python -m job_search.pipeline --check-config
```

## Deployment

A tracked `job_search_config.py` is picked up automatically — an intentional
exception to the deny-by-default `.gitignore`; it must contain reviewed code
only. GitHub Actions also supports a multiline `JOB_SEARCH_CONFIG_PY` secret;
the daily, manual-tailor, and base-render workflows each materialize it to
`job_search_config.py` after checkout, taking precedence over a tracked copy —
a transport for source, not a place for credentials. Install any imports your
module needs in the workflow yourself.

On a persistent host such as a Raspberry Pi, either keep a reviewed
`job_search_config.py` in the checkout or point `.env` at a file kept
elsewhere: `JOB_SEARCH_CONFIG_FILE=/home/pi/job-search-config/production.py`.

The core and the example are Python 3.9-compatible and standard-library only.
