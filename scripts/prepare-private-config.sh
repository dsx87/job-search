#!/usr/bin/env bash
#
# Prepare or verify the private, commit-pinned deployment configuration.
#
# --sync performs the only private-config fetch in this repository. --check-only
# is deliberately read-only and network-free, so systemd and manual pipeline
# invocations can reject drift before any delivery work begins.
set -euo pipefail

usage() {
  echo "Usage: scripts/prepare-private-config.sh --sync | --check-only" >&2
  exit 2
}

fail() {
  echo "private-config: $*" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
MODE="$1"
case "$MODE" in
  --sync|--check-only) ;;
  *) usage ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
DEPLOYMENT_ENV="$REPO/.deployment.env"
CONFIG_DIR="$REPO/.private-config/job-search-config"
readonly REPO DEPLOYMENT_ENV CONFIG_DIR

[ -f "$DEPLOYMENT_ENV" ] || fail "missing .deployment.env; create it from deployment.env.example"

# The local deployment file is trusted host configuration. It deliberately
# contains no credentials; .env remains the separate credential store.
set -a
. "$DEPLOYMENT_ENV"
set +a

CONFIG_REPOSITORY="${CONFIG_REPOSITORY:-}"
CONFIG_REF="${CONFIG_REF:-}"
CONFIG_SSH_KEY="${CONFIG_SSH_KEY:-$HOME/.ssh/job_search_config_ed25519}"

[[ "$CONFIG_REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || \
  fail "CONFIG_REPOSITORY must be owner/repository"
[[ "$CONFIG_REF" =~ ^[0-9a-f]{40}$ ]] || \
  fail "CONFIG_REF must be a full 40-character commit SHA"
[ -f "$CONFIG_SSH_KEY" ] && [ -r "$CONFIG_SSH_KEY" ] || \
  fail "CONFIG_SSH_KEY must name a readable private key"

EXPECTED_REMOTE="git@github.com:${CONFIG_REPOSITORY}.git"
printf -v SSH_KEY_ARG '%q' "$CONFIG_SSH_KEY"
GIT_SSH_COMMAND="ssh -i ${SSH_KEY_ARG} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

# `git status` normally refreshes the index. Check-only is used before every
# delivery run, so ensure its verification queries cannot create lock files or
# update checkout metadata.
git_readonly() {
  GIT_OPTIONAL_LOCKS=0 git "$@"
}

check_checkout() {
  [ -d "$CONFIG_DIR/.git" ] || fail "missing private config checkout; run --sync"
  remote="$(git_readonly -C "$CONFIG_DIR" config --get remote.origin.url 2>/dev/null || true)"
  [ "$remote" = "$EXPECTED_REMOTE" ] || fail "private config origin does not match CONFIG_REPOSITORY"
  [ -z "$(git_readonly -C "$CONFIG_DIR" status --porcelain --untracked-files=all)" ] || \
    fail "private config checkout has local changes; commit or resolve them before continuing"
  head="$(git_readonly -C "$CONFIG_DIR" rev-parse HEAD 2>/dev/null || true)"
  [ "$head" = "$CONFIG_REF" ] || fail "private config checkout does not match CONFIG_REF"
  [ -f "$CONFIG_DIR/job_search.toml" ] || fail "private config checkout has no job_search.toml"
}

check_existing_checkout_for_sync() {
  [ -d "$CONFIG_DIR/.git" ] || fail "private config checkout is not a Git repository"
  remote="$(git_readonly -C "$CONFIG_DIR" config --get remote.origin.url 2>/dev/null || true)"
  [ "$remote" = "$EXPECTED_REMOTE" ] || fail "private config origin does not match CONFIG_REPOSITORY"
  [ -z "$(git_readonly -C "$CONFIG_DIR" status --porcelain --untracked-files=all)" ] || \
    fail "private config checkout has local changes; commit or resolve them before continuing"
}

if [ "$MODE" = "--sync" ]; then
  if [ -e "$CONFIG_DIR" ]; then
    check_existing_checkout_for_sync
  else
    mkdir -p "$(dirname "$CONFIG_DIR")"
    umask 077
    GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
      git clone --quiet "$EXPECTED_REMOTE" "$CONFIG_DIR"
    # Git URL rewrite rules may provide the transport on a host. Keep the
    # checkout's recorded origin canonical so subsequent verification remains
    # independent of those local transport details.
    git -C "$CONFIG_DIR" remote set-url origin "$EXPECTED_REMOTE"
  fi

  # A clean, existing checkout may move only to the exact requested object.
  # There is intentionally no pull, reset, clean, or push here.
  check_existing_checkout_for_sync
  GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
    git -C "$CONFIG_DIR" fetch --quiet --depth 1 origin "$CONFIG_REF"
  git -C "$CONFIG_DIR" cat-file -e "${CONFIG_REF}^{commit}" || \
    fail "CONFIG_REF is not a commit available from the private config repository"
  git -C "$CONFIG_DIR" checkout --quiet --detach "$CONFIG_REF"
fi

check_checkout
echo "Private configuration is pinned and ready."
