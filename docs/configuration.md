# Configuration

Each fetch runs from a selected, versioned TOML file. Keep a candidate's search
terms, eligibility, CV paths, and policy data in a private configuration
repository; the checked-in [`job_search.example.toml`](../job_search.example.toml)
is a complete fictional reference for Avery Example.

```bash
export JOB_SEARCH_SETTINGS_FILE="$PWD/.private-config/job-search-config/job_search.toml"
python3 -m job_search --validate-config "$JOB_SEARCH_SETTINGS_FILE"
python3 -m job_search.pipeline --check-config
```

There is no implicit personal profile. Catalog defaults are deliberately
generic: candidate identity, CV paths, legal countries, employer order, and
private placeholders are blank or empty. Fetching requires both a selected
TOML file and at least one explicit `[search]` setting from TOML, the
environment, or an explicit programmatic override. A file containing only
`[settings].version = 1` is valid data but cannot fetch. The error points to
the public example and `JOB_SEARCH_SETTINGS_FILE`.

The TOML has four tables:

| Table | Owns |
|---|---|
| `[settings]` | providers, files, output, prompts, workers, and state |
| `[search]` | sources, terms, locations, age, and opportunity scope |
| `[candidate]` | CV identity, legal eligibility, placeholders, and page limits |
| `[policy]` | ordered built-in eligibility checks and declarative values |

`[settings].version = 1` is required. Unknown keys, unsupported types,
credentials, and host-only controls are errors. Python 3.11+ uses `tomllib`;
Python 3.9–3.10 needs `tomli>=2.0,<2.4`.

## Inspect and validate

```bash
# JSON catalog; it does not need a selected configuration file.
python3 -m job_search --describe-config

# Strictly parse this one TOML file as data.
python3 -m job_search --validate-config path/to/job_search.toml

# Load the selected configuration, build the trusted runtime, and check host tools.
python3 -m job_search.pipeline --check-config
```

`--describe-config` prints versioned JSON from
`job_search.settings.option_metadata()`: every option's type, generic default,
environment aliases, sensitivity, supported-in-file status, and constraints.
It also lists host controls including the active GitHub Actions private-config
controls. `--validate-config` is pure data validation: it reads no environment
overrides and does not run hooks, services, state, sources, or host/toolchain
checks. `--check-config` is the operational command. It loads environment
overrides, may execute reviewed Python, constructs the runtime, checks tools,
and reports redacted effective settings. Do not use it on an untrusted checkout.

`--describe-config`, `--validate-config`, and `--list-sources` remain useful
without selected settings. Fetching commands do not.

The public TUI can open, browse, and mark the existing local job history without
selected settings. Its refresh action fetches, so refresh requires the selected
TOML and explicit search setting.

## Private deployment configuration

On a persistent host, copy [`deployment.env.example`](../deployment.env.example)
to mode-600 `.deployment.env`. It contains only the private configuration
repository, its full commit SHA, a local deploy-key path, and optional
non-secret runtime overrides. The only operation that fetches configuration is:

```bash
scripts/prepare-private-config.sh --sync
scripts/prepare-private-config.sh --check-only
```

`--sync` checks out `CONFIG_REPOSITORY` at lowercase 40-hex `CONFIG_REF` into
`.private-config/job-search-config`. `--check-only` is
read-only and network-free; it verifies the deployment file, checkout
cleanliness, remote, exact HEAD, and `job_search.toml`. Both host wrappers load
`.deployment.env` first and then `.env`, and select
`.private-config/job-search-config/job_search.toml`.

`.env` stays a separate mode-600 credential file for LLM, Telegram, Telegraph,
and private CV values. Never place credentials, contact details, or CV history
in `.deployment.env`, the TOML example, or this public repository. GitHub
Actions uses Variables for `PERSONAL_RUNS_ENABLED`, `CONFIG_REPOSITORY`, and
`CONFIG_REF`; `CONFIG_DEPLOY_KEY` is an Actions Secret. The deployment workflow
verifies the pinned private checkout before a manual or scheduled run.

For catalogued settings, precedence is explicit programmatic override, first
nonempty environment alias, TOML, then catalog default. Empty environment
values do not override TOML. TOML input paths are relative to the TOML file;
TOML output paths are relative to the process working directory. Environment
path aliases retain working-directory behavior.

## Search, candidate, and policy data

Use `[search]` for role terms, required skill groups, location exclusions,
source selection, query terms and locations, age, and remote/relocation scope.
Skill groups are AND across groups and OR within each group. `sources_enable`
and `sources_disable` contain unique known source names and cannot overlap.

Selected generic query sources use `search_terms`, `query_locations`, and
`results_per_query`; they never fall back to a prior candidate's query. Empty
`query_locations` gives those sources no location loop, so they skip rather
than search an unstated scope. Arc, Mobile.Career, JobScroller, and LinkedIn
Israel are specialized explicit-only sources. Inspect names with
`python3 -m job_search --list-sources` before enabling one.

`remote_allowed` and `relocation_allowed` govern their respective opportunity
gates. An explicitly local role remains eligible when its advertised country is
in both `candidate.residency_countries` and
`candidate.work_authorization_countries`, independently of those gates.

Candidate country codes are ISO 3166-1 alpha-2. Country page-limit map keys
accept lowercase or uppercase and canonicalize to uppercase; non-ISO codes such
as `XK` and `ZZ` are rejected. The renderer uses an exact advertised country
limit, then `EU`, then `candidate.max_pages` (fallback default **1**) and takes
the smallest result for a multi-location role. Broad locations such as Europe
and EMEA do not imply EU. The United Kingdom, Switzerland, and Norway are
non-EU. Base-CV rendering validates the fallback limit only; it does not
auto-shrink, repair, or apply advertised-location rules.

`candidate.display_name` is tailoring-prompt context only; the base TeX file
owns the rendered identity. `private_placeholders` maps placeholder names to
environment variable names matching `^[A-Z_][A-Z0-9_]*$`. The map is not secret,
but resolved values are sensitive.

Search and evaluation commands accept blank CV identity and path fields. Render
operations require the relevant CV inputs. In particular,
`python3 -m job_search.latex.render_base` requires both `base_tex_file` and
`rendered_base_file`; tailored rendering also needs its configured base and
prompt inputs.

`[policy]` selects known, unique built-in check identifiers; TOML cannot add
Python predicates. The compatible `nonremote_work_authorization` identifier
applies explicit work-authorization requirements to every arrangement,
including remote. Residency never supplies authorization.

`criteria_file` is a readable compatibility input whose contents affect only
the reevaluation fingerprint. Editing it can reopen rejected jobs and trigger
new LLM work, but it is neither evaluator context nor executable policy.
Structured policy belongs in `[policy]`. `cv_tailoring_prompt_file` is another
legacy compatibility input: it is parsed for file validation and its text is
ignored by tailoring. To customize active prompts, set `prompt_dir` together
with `prompt_revision` and provide `cv_bullet_selection.txt`; the same
directory may override `fact_extraction.txt`, `job_summary.txt`, and
`compiler_repair.txt`.

## Digest sections and the Python escape hatch

`sections.example.py` is a fictional presentation example. Section order is
priority and each job appears once. A load or definition error yields an
ungrouped digest with a warning. A predicate blank-entry smoke warning retains
otherwise valid sections because a legitimate predicate can need real job data.
Sections never change search, policy, CVs, or delivery.

`job_search_config.py` is a rare, reviewed local-host escape hatch for behavior
no catalogued option can express. It runs with host credentials, so never use
it for secrets or normal search configuration. `JOB_SEARCH_CONFIG_FILE` names
it; if the variable is absent, the runtime optionally checks
`job_search_config.py` in the working directory. An explicit blank value is an
error. The Actions private-config checkout does not select a Python hook.
`JOB_SEARCH_CONFIG_PY` and `SECTIONS_PY` remain legacy transport metadata, not
the private-config deployment mechanism.
