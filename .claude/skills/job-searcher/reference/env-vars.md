# Environment variables

The selected TOML is the normal configuration surface. Run
`python3 -m job_search --describe-config` for the versioned machine-readable
catalog and see `job_search.example.toml` for the complete fictional example.
The catalog lists each option's aliases, generic default, type, sensitivity, and
whether TOML supports it. Fetching needs a selected TOML plus an explicit
`[search]` setting; environment overrides alone do not restore a personal
profile.

Use `python3 -m job_search.pipeline --check-config` only on a trusted host to
inspect resolved configuration. It can execute reviewed Python. For pure TOML
validation, use `python3 -m job_search --validate-config path/to/job_search.toml`.

## Host files

Copy `deployment.env.example` to mode-600 `.deployment.env`. It contains
`CONFIG_REPOSITORY`, lowercase 40-hex `CONFIG_REF`, `CONFIG_SSH_KEY`, and optional
non-secret runtime overrides. `scripts/prepare-private-config.sh --sync`
performs the only checkout into `.private-config/job-search-config`;
`--check-only` is network-free and verifies its origin, cleanliness, exact
HEAD, and `job_search.toml`.

Keep credentials and private CV placeholder values in separate mode-600 `.env`.
Host wrappers load `.deployment.env` first, then `.env`, and select the
nested `job_search.toml`. Never put secrets in `.deployment.env`.

## Credentials

These values are environment-only and must not enter TOML:

| Variable | Purpose |
|---|---|
| `LLM_PRIMARY_API_KEY` | primary provider key; legacy `GEMINI_API_KEY` remains an alias |
| `LLM_FALLBACK_API_KEY` | fallback provider key; legacy `OPENAI_API_KEY` remains an alias |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram delivery credential and recipient |
| `TELEGRAPH_ACCESS_TOKEN` | Telegraph publishing credential |
| CV placeholder variables such as `CV_PHONE` | resolved from a non-secret placeholder map but sensitive values |

## Provider and runtime overrides

The selected TOML owns provider, output, state, and CV choices. Environment
aliases override it only when nonempty. Current aliases include
`LLM_PRIMARY_SCHEME`, `LLM_PRIMARY_MODEL`, `LLM_PRIMARY_API_BASE`,
`LLM_PRIMARY_AUTH_MODE`, `LLM_FALLBACK_SCHEME`,
`LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_BASE`,
`LLM_FALLBACK_AUTH_MODE`, `OUTPUT_MODE`, `OUTPUT_DIR`,
`OUTPUT_CV_MODE`, `DIGEST_DELIVERY`, `STATE_SYNC`,
`EVAL_WORKERS`, `TAILOR_WORKERS`, `SCRAPE_BUDGET_SECONDS`,
`SOURCES_ENABLE`, `SOURCES_DISABLE`, `CV_MAX_PAGES`,
`CV_DISPLAY_NAME`, `CV_FILENAME_PREFIX`, `BASE_TEX_FILE`, and
`OUT_PDF_FILE`.

Host-only tuning values are `LATEX_MAX_WORKERS` (legacy
`XELATEX_MAX_WORKERS`), `LLM_BREAKER_THRESHOLD`, and
`ANTHROPIC_MAX_TOKENS`. `LINKEDIN_BUDGET_SECONDS` is a positive Guest-source
budget; when unset it is `max(60, SCRAPE_BUDGET_SECONDS * 0.85)`.

`JOB_SEARCH_CONFIG_FILE` is a trusted Python hook path: absent optionally
checks `job_search_config.py`, an explicit empty value errors, and a nonempty
path executes reviewed code at runtime. `JOB_SEARCH_CONFIG_PY` and
`SECTIONS_PY` are legacy Actions transport metadata, not configuration
checkout inputs.

## GitHub Actions

Actions Variables: `PERSONAL_RUNS_ENABLED`, `CONFIG_REPOSITORY`, and
`CONFIG_REF`. `PERSONAL_RUNS_ENABLED` must be exactly `true` for scheduled
private runs. Actions Secret: `CONFIG_DEPLOY_KEY`, a read-only deploy key for
the private configuration repository. Manual runs preflight these controls and
fail if they are incomplete; workflow checkout uses the configured full SHA.
