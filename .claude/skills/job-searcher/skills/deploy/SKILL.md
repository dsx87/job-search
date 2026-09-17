---
name: deploy
description: Deploy and operate AI Job Hunter with the private, commit-pinned configuration checkout on GitHub Actions or a Raspberry Pi host.
when_to_use: Triggered by requests to set up or diagnose scheduled runs, private configuration checkout, host deployment, Actions, timers, or rollout.
argument-hint: [actions | pi | config | triage]
disable-model-invocation: true
---

# Deploy and operate the job searcher

All fetches use a selected private TOML. Public repository examples are fictional
and must not become a deployment profile. The private configuration checkout is
always `.private-config/job-search-config`, with
`job_search.toml` at its root.

Confirm before enabling a timer, changing a live Actions variable or secret,
starting/restarting a service, or otherwise affecting a runner. Never print a
secret or copy it into a public file.

## Private configuration on a host

1. Copy `deployment.env.example` to mode-600 `.deployment.env`.
2. Put `CONFIG_REPOSITORY`, an exact lowercase 40-character `CONFIG_REF`, and a
   readable local `CONFIG_SSH_KEY` in it. It may include non-secret host
   overrides.
3. Keep LLM, Telegram, Telegraph, and private CV values in separate mode-600
   `.env`.
4. Run `scripts/prepare-private-config.sh --sync`.
5. Run `scripts/prepare-private-config.sh --check-only`, then
   `python3 -m job_search.pipeline --check-config` on the trusted host.

The sync command is the only private-config fetch. It detached-checks out the
configured revision and verifies the remote. Check-only makes no network calls;
it requires a clean nested checkout with matching origin, exact HEAD, and
`job_search.toml`. Host wrappers load `.deployment.env` before `.env` and
default `JOB_SEARCH_SETTINGS_FILE` to the nested TOML. Neither file is
rewritten by the helper.

A selected TOML plus at least one explicit `[search]` setting is required
before fetch. `--describe-config` and `--validate-config path` remain safe
ways to inspect configuration without a selected file; `--check-config` may
execute trusted Python.

## GitHub Actions

Use Actions Variables for `PERSONAL_RUNS_ENABLED`,
`CONFIG_REPOSITORY`, and `CONFIG_REF`; use an Actions Secret for
`CONFIG_DEPLOY_KEY`. The daily scheduled job skips unless the flag is exactly
`true`. Manual daily and tailoring workflows preflight all private-config
controls and fail when any is missing. They checkout the configured private
repository at the full SHA into `.private-config/job-search-config`, verify
HEAD, and use `persist-credentials: false`.

The render-base and one-page guard workflows use only the fictional public CV
fixture. They must not require or materialize a personal configuration.

## Raspberry Pi and triage

`bash scripts/setup-rpi.sh` prepares the host but does not enable timers,
start services, or change bot state. After the private checkout passes
check-only and runtime check, an operator can deliberately enable or run the
appropriate service.

For failures, inspect the run log and first verify the private checkout:
missing `.deployment.env`, malformed repository/full SHA, unreadable deploy
key, a dirty checkout, remote mismatch, wrong HEAD, or missing
`job_search.toml` are configuration failures. Then check the selected TOML,
its explicit search settings, protected credentials in `.env`, and host
prerequisites reported by `--check-config`.
