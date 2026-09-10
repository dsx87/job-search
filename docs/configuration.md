# Configuration

The normal configuration is a versioned TOML file, selected with
`JOB_SEARCH_SETTINGS_FILE`. Start with the complete, fictional
[`job_search.example.toml`](../job_search.example.toml): it contains every
catalogued option, its type, default, environment name, valid values,
dependencies, and effect. Copy it outside the repository if it names private
CV files, then replace the fictional candidate and search values.

```bash
cp job_search.example.toml ~/job-search-settings.toml
export JOB_SEARCH_SETTINGS_FILE="$HOME/job-search-settings.toml"
python3 -m job_search --validate-config "$JOB_SEARCH_SETTINGS_FILE"
```

The TOML format is deliberately data-only. It has four tables:

| Table | Owns |
|---|---|
| `[settings]` | provider, files, delivery, prompts, workers, and state |
| `[search]` | source selection, query terms, role/skill/location filtering, and opportunity scope |
| `[candidate]` | CV identity, private template placeholders, legal eligibility, and page limits |
| `[policy]` | ordered built-in eligibility checks and their declarative values |

`[settings].version = 1` is required. Unknown sections and keys, bad types,
unknown source names, invalid country codes, duplicate check IDs, and
environment-only keys in TOML are errors. Python 3.11+ uses `tomllib`; Python
3.9–3.10 needs `tomli>=2.0,<2.4`.

## Inspect and validate before a run

Three commands intentionally have different trust boundaries:

```bash
# Versioned machine-readable catalog: options, type, defaults, env names,
# sensitivity, and whether each setting is supported in TOML.
python3 -m job_search --describe-config

# Strictly parse exactly this TOML file. No environment override, customization
# hook, state, scraper, LLM, service, or host/toolchain check runs.
python3 -m job_search --validate-config ~/job-search-settings.toml

# Load selected settings with overrides, execute reviewed custom Python if
# present, construct the runtime, and check actual host prerequisites.
python3 -m job_search.pipeline --check-config
```

`--describe-config` prints JSON from `job_search.settings.option_metadata()`.
Its `environment` mapping also documents the settings selector, trusted-hook
and sections transport, the LinkedIn Guest budget, and four **planned** Actions
migration controls (`PERSONAL_RUNS_ENABLED`, `CONFIG_REPOSITORY`, `CONFIG_REF`,
and `CONFIG_DEPLOY_KEY`). Those controls are metadata only and are not active in PR1.
It does not even require the selected TOML file to exist.
`--validate-config`
is safe for CI and code review because it accepts an explicit path and treats
it only as data. `--check-config` is the operational check: it may execute the
trusted `job_search_config.py` escape hatch, checks host tools such as LaTeX,
and prints redacted effective settings. Do not treat
it as a safe parser for unreviewed repositories.

The same flags work with `python3 -m job_search.pipeline`; the ordinary
scraper command is useful when the configuration must be checked before a
pipeline runtime could be built.

## Precedence, paths, and environment

For every setting, the winner is: explicit programmatic override, then the
first nonempty environment variable listed in the catalog, then TOML, then the
catalog default. `LoadedSettings.origins` and `--check-config` identify the
winning source. Empty environment values do not override TOML.

With no selected TOML file, the PR1 compatibility path retains the repository's
legacy personal defaults. They intentionally differ from the generic catalog
defaults shown by `--describe-config`, which describes the versioned TOML
format. Select TOML before relying on catalog defaults for a reusable profile,
and use `--check-config` to inspect a host's actual effective values.

Use [`deployment.env.example`](../deployment.env.example) as the host and
GitHub Actions reference. It contains only fictional placeholders. Tokens, API
keys, chat IDs, and private CV values are sensitive and environment-only; TOML
rejects them. The example also lists current legacy aliases:
`GEMINI_MODEL`, `GEMINI_API_KEY`, `GEMINI_API_BASE`, `OPENAI_API_KEY`, and
`XELATEX_MAX_WORKERS`.

Input paths written in TOML (`criteria_file`, prompt files, `sections_file`,
and `candidate.base_tex_file`) are relative to the TOML file. Output paths
(`seen_jobs_file`, `output_dir`, and `candidate.rendered_base_file`) are
relative to the process working directory. Environment path aliases retain the
legacy working-directory behavior for both kinds of path. Absolute paths stay
absolute.

On a persistent host, put sensitive variables in a mode-600 `.env` or the
service manager's protected environment. In GitHub Actions, use Secrets for
credentials and private placeholder values; use repository Variables for
non-secret provider controls. `JOB_SEARCH_CONFIG_PY` and `SECTIONS_PY` define
the source-transport contract for an Actions migration, not a claim that every
workflow already materializes them. They are never a place for secrets.

## Search, candidate, and policy data

The example uses a fictional platform-engineering search, not an iOS profile.
`role_include_terms` and `role_exclude_terms` filter titles;
`skill_include_groups` is AND across groups and OR inside each group;
`location_exclude_terms` examines advertised location text. `search_terms`,
`query_locations`, and `results_per_query` are the provider-neutral source
query controls. `sources_enable` and `sources_disable` are alternatives for
default source selection; inspect valid names with `python3 -m job_search
--list-sources` and never put the same source in both.

With a selected versioned file, generic query-capable sources (JobSpy, LinkedIn
JobSpy and Guest, WorkingNomads, RelocateMe, and SecretTelAviv) use
`search_terms`, `query_locations`, and `results_per_query`; they do not fall
back to embedded iOS or Israel queries. Empty `query_locations` supplies no
location loops for those query sources, so they skip with a diagnostic instead
of silently searching a legacy scope. Provide both query arrays for the sources
you enable. LinkedIn Israel, Arc, Mobile.Career, and JobScroller are
specialized/explicit-only sources, so enable them only for profiles they fit.

`remote_allowed` and `relocation_allowed` govern their respective remote and
relocation gates and may both be true. Set one false to narrow those gates.
An explicitly local role remains eligible when its advertised country appears
in both `candidate.residency_countries` and
`candidate.work_authorization_countries`, independently of these switches.
`relocation_regions` applies only when relocation is enabled. `max_age_days =
0` removes the age cutoff.

Candidate residency and work authorization use ISO 3166-1 alpha-2 codes. The
policy applies only the named built-in IDs in `policy.check_order`; TOML cannot
provide Python predicates or change their implementation. A model-derived
rejection is a non-fit only when the evidence is grounded in the job posting;
otherwise it is reviewable.

`nonremote_work_authorization` remains the compatible check identifier, but its
current behavior applies every explicit work-authorization requirement to every
advertised arrangement, including remote. Residency never implies work
authorization; configure it separately in
`candidate.work_authorization_countries`.

`criteria_file` is retained as a required, readable compatibility input and
its contents contribute to the reopen fingerprint. Editing the prose can
reopen previously rejected jobs and incur new LLM work. The evaluator does not read it:
it supplies neither LLM context nor executable rules. Structured
eligibility behavior lives in `[policy]`; make policy changes there, then
validate and check the configuration before the next scheduled run.

Likewise, `cv_tailoring_prompt_file` is a legacy compatibility input. CV
tailoring parses it during preflight to preserve the old file-format validation,
then ignores its instruction text. To customize the active CV-selection prompt,
set `prompt_dir` and `prompt_revision`, then provide
`cv_bullet_selection.txt` in that directory. The same directory can override
`fact_extraction.txt`, `job_summary.txt`, and `compiler_repair.txt`; missing
files use their individual built-in prompts.

### CV page limits use advertised locations only

`candidate.max_pages` is the fallback and defaults to **1**. For each advertised
location, the renderer chooses an exact ISO country rule from
`max_pages_by_country`, otherwise an `EU` rule from `max_pages_by_region`, then
the fallback. It takes the smallest resulting number for a multi-location
posting. Thus a `Germany / France` role with `DE = 3` and `FR = 2` uses two
pages; a French role with no `FR` rule can use the `EU` rule; `Europe`, `EMEA`,
and unrecognized locations use the fallback. The United Kingdom, Switzerland,
and Norway are explicitly non-EU. Broad geography is never guessed to be EU,
and arbitrary profile location is not used. Base-CV rendering validates only
the fallback `max_pages`; it does not auto-shrink, repair, or apply an
advertised-location override.

`max_pages_by_country` accepts a valid ISO 3166-1 alpha-2 key in either case
and canonicalizes it to uppercase; invalid/non-ISO codes such as `XK` and `ZZ`
are rejected. `max_pages_by_region` accepts only `EU`. `CV_MAX_PAGES` is the
environment override for the fallback only.

## Delivery and digest choices

`output_mode` chooses `telegram`, `html`, or `plain`. `telegram` requires
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `output_cv_mode = "required"`.
`html` and `plain` write into `output_dir`; they can set
`output_cv_mode = "disabled"` to skip all tailoring and compilation. A manual
`--tailor` requires CV output.

With `digest_delivery = true`, a run sends one digest/archive instead of the
legacy per-job stream. Set `TELEGRAPH_ACCESS_TOKEN` to publish the digest as a
telegra.ph page with an AES-256 CV archive uploaded to x0.at. If encryption or
upload fails, delivery falls back to the Telegram ZIP. Digest sections are
presentation only: they never change search, policy, CVs, or delivery.

To group a digest locally, copy the fictional example:

```bash
cp sections.example.py sections.py
```

The configured `sections_file` is optional. Its first matching section wins;
unmatched jobs remain under an automatic Other heading. A load or definition
error falls back to an ungrouped digest with a warning. A predicate that fails
only during the blank-entry smoke check keeps the valid sections and reports a
warning, because a legitimate predicate can require real job data.

## The Python escape hatch

Use `job_search_config.py` only when no catalogued setting can express a real
need. It is trusted executable Python, loaded after built-in runtime assembly
and before host preflight. It is intentionally unvalidated: exceptions from
your code remain your traceback. Never put credentials or private CV values in
it.

```python
def configure(runtime, settings):
    # Example only: retain jobs with an explicitly tagged internal source.
    runtime.candidate_filter = lambda job: job.source == "example-source"
    return runtime
```

Copy [`job_search_config.example.py`](../job_search_config.example.py), edit it
as reviewed source, and run `--check-config` on the target host. When
`JOB_SEARCH_CONFIG_FILE` is absent, the runtime optionally loads
`job_search_config.py` from the working directory if it exists. An explicit
nonempty value must name an existing file; an explicit blank value is an error.
On Actions, `JOB_SEARCH_CONFIG_PY` materializes trusted source after checkout.
It takes precedence over a tracked local hook but does not grant a safe place
for credentials.
