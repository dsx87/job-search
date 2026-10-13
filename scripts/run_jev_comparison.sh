#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
local_dir="$repo_dir/.jev-comparison/jev-only"
credential_file="$repo_dir/.jev-comparison/credentials.env"

if [[ -f "$credential_file" ]]; then
  jev_from_env="${JEV_API_KEY:-}"
  jev_url_from_env="${JEV_API_URL:-}"
  jev_model_from_env="${JEV_MODEL:-}"
  set -a
  # This ignored file is local shell input; put only KEY=value assignments in it.
  source "$credential_file"
  set +a
  [[ -z "$jev_from_env" ]] || export JEV_API_KEY="$jev_from_env"
  [[ -z "$jev_url_from_env" ]] || export JEV_API_URL="$jev_url_from_env"
  [[ -z "$jev_model_from_env" ]] || export JEV_MODEL="$jev_model_from_env"
fi

if [[ -z "${JEV_API_KEY:-}" || "${JEV_API_KEY}" == REPLACE_* ]]; then
  echo "Set JEV_API_KEY in $credential_file or the environment." >&2
  exit 2
fi
if [[ "${JEV_API_URL:-}" == REPLACE_* ]]; then
  echo "Set JEV_API_URL to the working DefAPI Jev decision endpoint in $credential_file." >&2
  exit 2
fi

cd "$repo_dir"
python -m scripts.compare_jev \
  --state "$repo_dir/../../job_state.json" \
  --settings "$repo_dir/../../.private-config/job-search-config/job_search.toml" \
  --local-dir "$local_dir" \
  "$@"
