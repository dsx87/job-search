# `job-searcher` skills

A [skills-directory plugin](https://code.claude.com/docs/en/plugins-reference#skills-directory-plugins)
checked into this repository. Because `.claude/skills/job-searcher/` carries a
`.claude-plugin/plugin.json`, Claude Code loads it as `job-searcher@skills-dir`
with no marketplace and no install step — the skills are simply there for anyone
who clones the repo and trusts the workspace.

| Skill | Purpose |
|---|---|
| `/job-searcher:configure` | environment settings, `criteria.md`, sources, `sections.py`, and the optional `job_search_config.py` escape hatch |
| `/job-searcher:deploy` | GitHub Actions and Raspberry Pi installs, dedup-state seeding/sync, operating a host, run triage |
| `/job-searcher:explain` | how the pipeline works — stages, sources, provider fallback, CV guards, delivery, and the local CLI/TUI |

`configure` and `explain` also load automatically when a request matches.
`deploy` is `disable-model-invocation: true` — it acts on live runners, so you
invoke it deliberately.

## Notes

- Launch Claude Code from the repository root. A project-scope `@skills-dir`
  plugin loads only from the primary working directory's `.claude/skills/`; it
  does not walk up from a subdirectory the way plain skills do.
- Edits to a `SKILL.md` take effect immediately. Edits to `plugin.json` need
  `/reload-plugins` or a restart.
- The skills are a map into the real documentation, not a copy of it. Depth
  lives in `docs/configuration.md`, `docs/deploy-rpi.md`, and the README; keep
  it that way when editing them, so there is one place to update.
