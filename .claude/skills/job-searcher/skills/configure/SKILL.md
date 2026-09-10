---
name: configure
description: Configure AI Job Hunter through a selected, versioned TOML file, protected environment variables, private deployment checkout, digest sections, or the rare reviewed Python escape hatch.
when_to_use: Triggered by requests to change searches, candidate eligibility, CV limits, providers, output, prompts, digest sections, or to inspect configuration.
argument-hint: [what to change]
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python3 -m job_search*), Bash(python -m job_search*)
---

# Configure the job searcher

Use a selected versioned TOML file for normal behavior. The public
`job_search.example.toml` is complete but fictional; copy it into the private
configuration repository, replace Avery Example values, and select it with
`JOB_SEARCH_SETTINGS_FILE`. Candidate identity, CV paths, legal countries,
employer order, and placeholders have generic blank defaults. There is no
legacy personal profile to fall back to.

A fetch requires both selected TOML and at least one explicit `[search]`
setting supplied by TOML, environment, or an explicit override. A
version-only TOML can be validated but cannot fetch.

| Layer | Mechanism | Use for |
|---|---|---|
| 1 | selected `job_search.toml` | versioned provider, search, candidate, policy, CV, and delivery data |
| 2 | protected environment | credentials, host tuning, and temporary overrides |
| 3 | `.deployment.env` | pinned private-config checkout and non-secret host overrides |
| 4 | `criteria.md` | reevaluation fingerprint compatibility input only |
| 5 | `sections.py` | digest presentation only |
| 6 | `job_search_config.py` | rare reviewed Python behavior missing from the catalog |

## Discovery and validation

```bash
python3 -m job_search --describe-config
python3 -m job_search --validate-config path/to/job_search.toml
python3 -m job_search.pipeline --check-config
```

`--describe-config` emits the versioned JSON catalog without loading selected
settings. `--validate-config` parses exactly the supplied TOML as data: no
environment overrides, hooks, state, services, sources, or host checks.
`--check-config` is for a trusted target host: it resolves overrides, may run
reviewed Python, builds the runtime, checks tools, and redacts sensitive values.
Never use it to inspect unreviewed configuration.

## Private configuration checkout

On a host, copy `deployment.env.example` to mode-600 `.deployment.env` and
put `CONFIG_REPOSITORY`, lowercase 40-hex `CONFIG_REF`, and `CONFIG_SSH_KEY` there.
Keep LLM, Telegram, Telegraph, and CV values in separate mode-600 `.env`.
Then run:

```bash
scripts/prepare-private-config.sh --sync
scripts/prepare-private-config.sh --check-only
```

The sync command is the only fetch path. It creates the detached, clean private
checkout at `.private-config/job-search-config`; check-only is network-free and
checks its remote, cleanliness, exact HEAD, and `job_search.toml`. Host wrappers
load `.deployment.env` before `.env` and select the nested TOML. Actions uses
Variables for `PERSONAL_RUNS_ENABLED`, `CONFIG_REPOSITORY`, and `CONFIG_REF`,
and an Actions Secret for `CONFIG_DEPLOY_KEY`.

## Configuration semantics

TOML precedence is explicit override, first nonempty environment alias, TOML,
then generic catalog default. TOML input paths are relative to the TOML and
output paths to the process working directory.

Use `[search]` for source controls, role terms, skill groups, locations, age,
and remote/relocation gates. Skill groups are AND across groups and OR within a
group. Generic query sources use `search_terms` and `query_locations` exactly;
empty locations skip those sources rather than choosing a legacy scope. Use
`--list-sources` before configuring a source name.

Use `[candidate]` for legal countries and CV data. Country map keys accept ISO
alpha-2 in either case and canonicalize to uppercase; `XK` and `ZZ` fail.
Tailored page limits use exact advertised country, then `EU`, then fallback
`max_pages` (default 1), taking the smallest result for multiple locations.
The UK, Switzerland, and Norway are non-EU. Base rendering validates only the
fallback and never auto-shrinks.

Search and evaluation accept blank CV identity and paths. Render operations
need their CV inputs: `python3 -m job_search.latex.render_base` requires both
`base_tex_file` and `rendered_base_file`; tailored rendering needs configured
base and prompt inputs.

Use `[policy]` for ordered, known built-in checks. The compatible
`nonremote_work_authorization` identifier checks explicit authorization for
all arrangements, including remote; residency never implies authorization.
`criteria.md` changes only the reevaluation fingerprint, never evaluator
context or policy. `cv_tailoring_prompt_file` is parsed only for compatibility
validation; active bullet instructions come from
`prompt_dir/cv_bullet_selection.txt` and require `prompt_revision`.

Sections affect presentation only. Load/definition errors yield ungrouped
output; a blank-entry predicate smoke warning retains otherwise valid sections.
Use `job_search_config.py` only as a reviewed local-host hook for code that no
catalog option can express. It runs with host credentials. An absent
`JOB_SEARCH_CONFIG_FILE` optionally checks the working-directory default; an
explicit blank is an error. The Actions private-config checkout does not select
a Python hook. `JOB_SEARCH_CONFIG_PY` and `SECTIONS_PY` are legacy transport
metadata, not the private deployment path.
