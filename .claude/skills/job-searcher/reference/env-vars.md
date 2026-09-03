# Environment variables

Every value below is read by `PipelineConfig.from_env()` or at import in
`job_search/config.py`. Blank and whitespace-only values fall back to the
default, so an empty variable is the same as an unset one.

Verify what a host actually resolves with `python3 -m job_search.pipeline --check-config`
(prints effective settings as JSON with keys and chat ids redacted).

## Credentials

| Variable | Required | Purpose |
|---|---|---|
| `LLM_PRIMARY_API_KEY` | yes | primary provider key; falls back to `GEMINI_API_KEY` |
| `TELEGRAM_BOT_TOKEN` | yes | delivery |
| `TELEGRAM_CHAT_ID` | yes | delivery target (the authorized chat) |
| `LLM_FALLBACK_API_KEY` | strongly recommended | fallback provider key; falls back to `OPENAI_API_KEY` |
| `CV_PHONE` | optional | replaces the `((PHONE))` placeholder at compile time; never committed, never sent to the LLM |
| `TELEGRAPH_ACCESS_TOKEN` | optional | switches delivery to a telegra.ph page + hosted encrypted CV archive |
| `TELEGRAPH_PREVIEW_TOKEN` | optional | separate account used only by `scripts/telegraph_preview.py` |

Without `LLM_FALLBACK_API_KEY`, a retired or rejected primary model is a total
outage rather than a degraded run. The default primary `gemini-2.5-flash` has an
announced shutdown date (`LLM_MODEL_SHUTDOWN_DATES` in `job_search/config.py`).

## Providers

A provider is a **scheme** + model + key (+ optional base). Switching providers
is configuration only.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PRIMARY_SCHEME` | `gemini` | `gemini` \| `openai` \| `anthropic` |
| `LLM_PRIMARY_MODEL` | `gemini-2.5-flash` | legacy `GEMINI_MODEL` still honored |
| `LLM_PRIMARY_API_BASE` | scheme default | legacy `GEMINI_API_BASE` still honored |
| `LLM_PRIMARY_AUTH_MODE` | `bearer` | `none` requires a non-default absolute HTTP(S) base |
| `LLM_FALLBACK_SCHEME` | `openai` | |
| `LLM_FALLBACK_MODEL` | `gpt-5.4-mini` | |
| `LLM_FALLBACK_API_BASE` | scheme default | e.g. `https://api.groq.com/openai/v1` |
| `LLM_FALLBACK_AUTH_MODE` | `bearer` | |
| `LLM_BREAKER_THRESHOLD` | `2` | consecutive 429/503 post-retry failures before the primary is disabled for the run |
| `ANTHROPIC_MAX_TOKENS` | `4096` | required by the Anthropic scheme; ignored by others |

The `openai` scheme covers any OpenAI-compatible endpoint (Groq, xAI, DeepSeek,
LM Studio) through its `*_API_BASE`.

## Files

| Variable | Default |
|---|---|
| `JOB_SEARCH_CONFIG_FILE` | `job_search_config.py` if present (optional) |
| `SEEN_JOBS_FILE` | `seen_jobs.json` |
| `CRITERIA_FILE` | `criteria.md` |
| `CV_TAILORING_PROMPT_FILE` | `cv_tailoring_prompt.md` |
| `BASE_TEX_FILE` | `igor_pivnyk_cv_base_updated.tex` |
| `OUT_PDF_FILE` | `igor_pivnyk_cv_base_updated.pdf` |
| `SECTIONS_FILE` | `sections.py` |

`JOB_SEARCH_CONFIG_FILE` must exist when set explicitly, and an *empty* value is
an error rather than "disabled"; the default path is optional. Changing
`criteria.md` changes the evaluation fingerprint, which reopens previously
rejected jobs.

## Output

| Variable | Default | Notes |
|---|---|---|
| `OUTPUT_MODE` | `telegram` | `telegram` \| `html` \| `plain` — picks the renderer/backend pair as a unit |
| `OUTPUT_DIR` | unset | filesystem destination for `html`/`plain`; unused for `telegram` |
| `OUTPUT_CV_MODE` | `required` | `required` \| `disabled` — disabled skips all CV work |
| `DIGEST_DELIVERY` | `1` | one digest per run; `0` reverts to legacy per-job messages |
| `TELEGRAPH_ACCESS_TOKEN` | unset | publish the digest as a telegra.ph page + one x0.at CV archive |

`OUTPUT_MODE=telegram` requires `OUTPUT_CV_MODE=required` — Telegram has no
text-only delivery path, and the pair is rejected at startup rather than failing
at delivery after a full run.

## Prompts and CV rendering

| Variable | Default | Notes |
|---|---|---|
| `PROMPT_DIR` | unset | directory of file-backed prompt overrides (`fact_extraction.txt`, `job_summary.txt`, `cv_bullet_selection.txt`, `compiler_repair.txt`); missing files fall back individually |
| `PROMPT_REVISION` | unset | **required** whenever `PROMPT_DIR` is set; feeds the reopen fingerprint |
| `LATEX_ENGINE` | `pdflatex` | LaTeX executable, e.g. `xelatex` |
| `LATEX_MAX_WORKERS` | `2` | concurrent compilations (legacy `XELATEX_MAX_WORKERS` still honored) |
| `CV_DISPLAY_NAME` | `Igor Pivnyk` | name on the CV and in prompts |
| `CV_FILENAME_PREFIX` | `igor_pivnyk_cv` | prefix of every tailored PDF |
| `CV_PHONE` | unset | substituted for `((PHONE))` at compile time only |

## Sources, tuning, delivery

| Variable | Default | Purpose |
|---|---|---|
| `SOURCES_ENABLE` | *(empty)* | comma list forcing default-off sources on (e.g. `linkedin-guest`) |
| `SOURCES_DISABLE` | *(empty)* | comma list forcing default-on sources off |
| `SCRAPE_BUDGET_SECONDS` | `600` | wall-clock ceiling on the whole fetch stage |
| `EVAL_WORKERS` | `12` | evaluation concurrency (Pi: 2) |
| `TAILOR_WORKERS` | `8` | tailoring concurrency (Pi: 1) |
| `LATEX_MAX_WORKERS` | `2` | concurrent `pdflatex` runs; legacy `XELATEX_MAX_WORKERS` honored |
| `DIGEST_DELIVERY` | `1` | `0` reverts to the legacy per-job message + attachment stream |
| `STATE_SYNC` | `0` | `1` git-syncs `seen_jobs.json` with the orphan `state` branch around the run |

A malformed integer never crashes a run: worker counts warn and use the default.

## GitHub Actions names

Actions **secrets**: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`OPENAI_API_KEY`, `CV_PHONE`, `TELEGRAPH_ACCESS_TOKEN`, `JOB_SEARCH_CONFIG_PY`.

The workflow maps `GEMINI_API_KEY` → `LLM_PRIMARY_API_KEY` and `OPENAI_API_KEY`
→ `LLM_FALLBACK_API_KEY`.

Actions **variables** (not secrets): the eight `LLM_*` provider overrides above
and `SECTIONS_PY`. `JOB_SEARCH_CONFIG_PY` and `SECTIONS_PY` carry *file
contents*, materialized after checkout; a runner never sees an untracked file.
