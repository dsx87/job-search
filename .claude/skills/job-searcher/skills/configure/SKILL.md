---
name: configure
description: Configure AI Job Hunter with versioned TOML, host environment controls, LLM providers, search and policy data, digest sections, or the rare trusted Python escape hatch. Use for requests that change what is searched, who is eligible, how CVs are limited, which model is called, or how a digest is delivered.
when_to_use: Triggered by requests like "switch the job search to Groq", "change the candidate's residency", "make EU CVs two pages", "search for platform engineers", "group the digest by region", "write the digest to disk", or "check my job-search config".
argument-hint: [what to change]
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python3 -m job_search*), Bash(python -m job_search*)
---

# Configure the job searcher

Use the least powerful layer that expresses the requested change.

| Layer | Mechanism | Use for |
|---|---|---|
| 1 | `job_search.toml` | versioned search, candidate, policy, provider, and delivery data |
| 2 | protected environment / Actions secrets and variables | credentials, host-specific paths, temporary overrides, and environment-only tuning |
| 3 | `criteria.md` | reopen-fingerprint compatibility input; it does not define evaluator context or policy |
| 4 | `sections.py` | digest presentation only |
| 5 | `job_search_config.py` | rare reviewed Python behavior with no catalogued setting |

Start with `job_search.example.toml` and `deployment.env.example`. The TOML
example has every supported option with type, default, env name, valid values,
dependencies, and effects. Treat it as the authoritative human reference;
`--describe-config` is the machine-readable reference.

## Read-only discovery and validation

Use the command matching the requested confidence level:

```bash
# JSON catalog: no selected settings file, environment, hook, service, state,
# or host check is loaded.
python3 -m job_search --describe-config

# Strict data-only validation of exactly this TOML. It ignores environment
# overrides and never executes hooks, touches state, calls services, or checks
# toolchains.
python3 -m job_search --validate-config job_search.toml

# Operational check on the actual deployment host. Loads selected settings and
# environment, executes trusted job_search_config.py if present, builds the
# runtime, checks host prerequisites, and redacts secrets in its JSON output.
python3 -m job_search.pipeline --check-config
```

`--describe-config` exposes versioned metadata for every setting: dotted key,
type, default, description, environment aliases, sensitivity, and whether TOML
supports it. Its `environment` mapping covers the settings selector and the
trusted-hook/sections transport controls. `--validate-config` is safe for unreviewed configuration because
it is pure data validation. Never use `--check-config` to inspect unreviewed
repositories: it intentionally runs the trusted Python escape hatch.

Without a selected TOML file, the PR1 compatibility path keeps repository
legacy personal defaults. They differ from the generic TOML catalog shown by
`--describe-config`; select TOML for reusable defaults and use `--check-config`
to inspect the effective host configuration.

## TOML and environment rules

`[settings].version = 1` is required. TOML rejects unknown keys, invalid types,
credentials, and host-only controls. Precedence is explicit programmatic
override, first nonempty environment alias, TOML, then catalog default. Empty
environment variables do not replace TOML values.

Input paths in TOML are relative to the TOML file; output paths are relative to
the process working directory. Credentials, chat IDs, and private template
values only belong in a protected host environment or Actions Secrets. The
legacy aliases `GEMINI_MODEL`, `GEMINI_API_KEY`, `GEMINI_API_BASE`,
`OPENAI_API_KEY`, and `XELATEX_MAX_WORKERS` remain supported during migration.

For GitHub Actions, store sensitive values in Secrets and normal provider
controls in Variables. `JOB_SEARCH_CONFIG_PY` and `SECTIONS_PY` are reviewed
source-transport controls for workflows that explicitly materialize them after
checkout; they are not secret storage.

## Search, candidate, and policy changes

Use `[search]` for title terms, required skill groups, location exclusions,
source selection, query terms/locations, age, and remote/relocation scope.
Skill groups are AND across groups and OR inside each group. `remote_allowed`
and `relocation_allowed` can both be true and govern only those two gates.
An explicitly local role is still eligible when its advertised country appears
in both candidate residency and work-authorization lists.
For selected generic query sources, supply both `search_terms` and
`query_locations`: empty query locations skip those sources with a diagnostic
rather than falling back to embedded legacy queries. Run `python3 -m job_search
--list-sources` before naming a source.

Use `[candidate]` for legal residence and work authorization (ISO alpha-2), CV
identity, private placeholder-to-environment mappings, and page limits. CV page
limits resolve from exact advertised ISO country, then `EU`, then the fallback
`max_pages` (default 1); multi-location postings take the smallest result.
Broad locations such as Europe and EMEA do not imply EU, and the UK,
Switzerland, and Norway are non-EU. Country keys accept either case and
canonicalize to uppercase valid ISO alpha-2 codes; `XK` and `ZZ` are rejected.
Base-CV rendering validates only fallback
`max_pages`; it does not auto-shrink or apply advertised-location overrides.

The retained `nonremote_work_authorization` policy-check identifier now applies
explicit authorization requirements to every advertised arrangement, including
remote. Do not infer authorization from residency; configure the two country
lists independently.

Use `[policy]` for named, ordered built-in checks. Configuration data cannot run
arbitrary predicates. A model claim must be grounded in the posting before it
rejects a job; otherwise it is reviewable.

`criteria.md` is a required compatibility input whose contents feed only the
reopen fingerprint. Tell the user that an edit can reopen rejected jobs and
cause new LLM work, but it never reaches evaluator prompts or changes
structured `[policy]` behavior. Make a policy edit in TOML instead.

`cv_tailoring_prompt_file` is also compatibility-only: the pipeline parses it
for legacy file-format validation, then the deterministic tailor ignores its
instruction text. To change the active CV bullet-selection prompt, set
`prompt_dir` and `prompt_revision` and add `cv_bullet_selection.txt`; the same
directory supports `fact_extraction.txt`, `job_summary.txt`, and
`compiler_repair.txt` with per-file built-in fallbacks.

## Digest sections and escape hatch

Copy `sections.example.py` to `sections.py` for optional presentation grouping.
Section order is priority and each job appears once. A load or definition error
falls back to an ungrouped digest; a predicate smoke-check error retains valid
sections and reports a warning. Sections never change search, policy, CVs, or
delivery.

Reach for `job_search_config.py` only when no catalogued setting expresses the
need. It is reviewed executable Python, deliberately unvalidated, and runs
with process credentials. Never put credentials or private CV data in it.
When `JOB_SEARCH_CONFIG_FILE` is absent, the runtime optionally checks the
working-directory `job_search_config.py`; an explicit nonempty path must exist,
and an explicit blank value is an error.

```python
def configure(runtime, settings):
    runtime.candidate_filter = lambda job: job.source == "example-source"
    return runtime
```

Finish every configuration change with the appropriate validation command; use
`--check-config` only on the intended, trusted deployment host.
