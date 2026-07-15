#!/usr/bin/env bash
# One-time (idempotent) setup on the Pi for seen_jobs.json <-> `state` branch sync.
# Creates a dedicated SSH deploy key + host alias and a .state checkout.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"   # repo root (this script lives in <repo>/scripts/)
KEY="$HOME/.ssh/job_search_state"
CFG="$HOME/.ssh/config"

mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"

# 1. Deploy keypair (ed25519, no passphrase -> usable from unattended systemd).
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "rpi-igor job-search state deploy key" >/dev/null
  echo "generated $KEY"
else
  echo "key already exists: $KEY"
fi
chmod 600 "$KEY"; chmod 644 "$KEY.pub"

# 2. SSH host alias 'github-state' -> github.com, using ONLY this key.
if ! grep -q "Host github-state" "$CFG" 2>/dev/null; then
  {
    echo ""
    echo "# job-search: push seen_jobs.json dedup state to GitHub via a dedicated deploy key"
    echo "Host github-state"
    echo "    HostName github.com"
    echo "    User git"
    echo "    IdentityFile $KEY"
    echo "    IdentitiesOnly yes"
    echo "    StrictHostKeyChecking accept-new"
  } >> "$CFG"
  chmod 600 "$CFG"
  echo "added 'Host github-state' to $CFG"
else
  echo "'Host github-state' already in $CFG"
fi

# 3. Pin github.com host keys so unattended runs never prompt.
touch "$HOME/.ssh/known_hosts"; chmod 644 "$HOME/.ssh/known_hosts"
if ! grep -q "github.com" "$HOME/.ssh/known_hosts" 2>/dev/null; then
  ssh-keyscan -t ed25519,rsa github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
  echo "pinned github.com host keys"
else
  echo "github.com already in known_hosts"
fi

# 4. .state = dedicated checkout of the `state` branch. Clone anonymously over
#    HTTPS now (public read needs no key), then point the remote at the SSH
#    deploy-key URL for future fetch/push.
if [ ! -d "$REPO/.state/.git" ]; then
  git clone --quiet --depth 1 --branch state https://github.com/dsx87/job-search.git "$REPO/.state"
  echo "cloned .state (state branch, $(python3 -c "import json;print(len(json.load(open('$REPO/.state/seen_jobs.json'))))" 2>/dev/null) keys)"
else
  echo ".state already exists"
fi
git -C "$REPO/.state" remote set-url origin "git@github-state:dsx87/job-search.git"
git -C "$REPO/.state" config user.name  "RPi-Igor"
git -C "$REPO/.state" config user.email "consul87@gmail.com"
echo "state remote -> $(git -C "$REPO/.state" remote get-url origin)"

# 5. Keep .state out of the main repo's git status.
EXCL="$REPO/.git/info/exclude"
grep -qx ".state/" "$EXCL" 2>/dev/null || echo ".state/" >> "$EXCL"

echo
echo "=== DEPLOY KEY PUBLIC HALF ==="
cat "$KEY.pub"
echo "=============================="
echo
echo "Next: add the key above as a WRITE deploy key on github.com/dsx87/job-search"
echo "(Settings → Deploy keys → Add, tick 'Allow write access'), then turn sync on:"
echo "    echo 'STATE_SYNC=1' >> $REPO/.env"
echo "The daily run will then pull seen_jobs.json from origin/state before, and push"
echo "it back after — look for '[state] pulled/pushed N keys' lines in the journal."
