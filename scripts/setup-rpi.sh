#!/usr/bin/env bash
#
# setup-rpi.sh — provision a Raspberry Pi to run the AI Job Hunter daily flow.
#
# Target: original Raspberry Pi Model B (ARMv6, ~512 MB) running Raspberry Pi
# OS Lite (32-bit). Also works on newer Pis. Safe to re-run (idempotent).
#
# What it does:
#   1. Installs Python 3, git, and a right-sized pdflatex + Helvetica font set
#   2. Bumps swap (pandas/pdflatex/pip spike past 512 MB)
#   3. Sets the timezone
#   4. Requires a pinned private configuration checkout and preserves .env secrets
#   5. Seeds seen_jobs.json from the origin/state branch (avoids the first-run trap)
#   6. Pre-warms pdflatex so the first timed compile isn't the cold one
#   7. Writes systemd units without changing timer or bot state
#
# What it does NOT do: flash the OS, put your secrets in, or run the (possibly
# multi-hour) first pipeline. You do those — see the printed next steps.
#
# Usage (from the repo root, after cloning on the Pi):
#   bash scripts/setup-rpi.sh
#
# Override defaults via env vars, e.g.:
#   TIMEZONE=Europe/Berlin RUN_TIME=06:30 SWAP_MB=2048 bash scripts/setup-rpi.sh
#   TRY_JOBSPY=1 bash scripts/setup-rpi.sh      # also attempt the 3 JobSpy sources
#
set -euo pipefail

# ---- Tunables (override via environment) -----------------------------------
TIMEZONE="${TIMEZONE:-Asia/Jerusalem}"
RUN_TIME="${RUN_TIME:-07:00}"                       # local HH:MM for the daily timer
SWAP_MB="${SWAP_MB:-1024}"
EVAL_WORKERS="${EVAL_WORKERS:-2}"                   # LLM filter concurrency (single core!)
TAILOR_WORKERS="${TAILOR_WORKERS:-1}"              # CV/pdflatex concurrency
SCRAPE_BUDGET_SECONDS="${SCRAPE_BUDGET_SECONDS:-600}"
TRY_JOBSPY="${TRY_JOBSPY:-0}"                       # 1 = attempt pandas/jobspy (usually fails on ARMv6)
SERVICE_NAME="job-search"

# ---- Helpers ---------------------------------------------------------------
c_blue='\033[1;34m'; c_green='\033[1;32m'; c_yellow='\033[1;33m'; c_red='\033[1;31m'; c_off='\033[0m'
step() { echo -e "\n${c_blue}==> $*${c_off}"; }
ok()   { echo -e "${c_green}    ✓ $*${c_off}"; }
warn() { echo -e "${c_yellow}    ! $*${c_off}"; }
die()  { echo -e "${c_red}ERROR: $*${c_off}" >&2; exit 1; }

# ---- Preflight -------------------------------------------------------------
[ "$(uname -s)" = "Linux" ] || die "This script provisions a Linux Raspberry Pi; run it ON the Pi, not on macOS."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
[ -f "$REPO/pyproject.toml" ] && [ -d "$REPO/job_search" ] || die "Can't find the repo root (expected $REPO to contain job_search/ and pyproject.toml)."
DEPLOYMENT_ENV="$REPO/.deployment.env"
[ -f "$DEPLOYMENT_ENV" ] || die "Missing $DEPLOYMENT_ENV. Copy deployment.env.example, set the private repository, full commit SHA, and deploy-key path before setup."

# Run privileged bits with sudo; run user-owned bits as the human, not root.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  REAL_USER="${SUDO_USER:-root}"
else
  command -v sudo >/dev/null 2>&1 || die "sudo not found and not running as root."
  SUDO="sudo"
  REAL_USER="$USER"
fi
REAL_HOME="$(eval echo "~$REAL_USER")"
readonly REPO DEPLOYMENT_ENV REAL_USER REAL_HOME

# Fail before mutating the host when the requested private configuration is
# incomplete. The helper repeats and fully verifies this after git is present.
set -a
. "$DEPLOYMENT_ENV"
set +a
CONFIG_REPOSITORY="${CONFIG_REPOSITORY:-}"
CONFIG_REF="${CONFIG_REF:-}"
CONFIG_SSH_KEY="${CONFIG_SSH_KEY:-$REAL_HOME/.ssh/job_search_config_ed25519}"
[[ "$CONFIG_REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "CONFIG_REPOSITORY in .deployment.env must be owner/repository."
[[ "$CONFIG_REF" =~ ^[0-9a-f]{40}$ ]] || die "CONFIG_REF in .deployment.env must be a lowercase full 40-character commit SHA."
[ -f "$CONFIG_SSH_KEY" ] && [ -r "$CONFIG_SSH_KEY" ] || die "CONFIG_SSH_KEY in .deployment.env must name a readable private key."

step "Provisioning for user '$REAL_USER' — repo at $REPO"
if grep -qiE 'ARMv6|BCM2835' /proc/cpuinfo 2>/dev/null; then
  ok "Detected ARMv6 (original Pi B family) — will skip Chromium; JobSpy is best-effort."
else
  warn "This does not look like an ARMv6 Pi — the script still works, just tuned conservatively."
fi

# ---- 1. APT packages -------------------------------------------------------
# --no-install-recommends keeps TeX Live from pulling ~GBs of docs onto the SD card.
step "Installing system packages (Python, git, pdflatex + Helvetica)"
$SUDO apt-get update -qq
# Helvetica (URW Nimbus Sans) ships in texlive-fonts-recommended, so no separate
# font package or fontconfig is needed. pdflatex is in texlive-binaries, pulled
# in as a dependency of texlive-latex-recommended.
$SUDO apt-get install -y --no-install-recommends \
  git ca-certificates python3 python3-venv \
  texlive-latex-recommended texlive-fonts-recommended

# ---- 2. Swap ---------------------------------------------------------------
step "Configuring ${SWAP_MB} MB swap"
if command -v dphys-swapfile >/dev/null 2>&1; then
  $SUDO dphys-swapfile swapoff || true
  $SUDO sed -i "s/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=${SWAP_MB}/" /etc/dphys-swapfile
  if grep -q '^CONF_MAXSWAP=' /etc/dphys-swapfile; then
    $SUDO sed -i "s/^CONF_MAXSWAP=.*/CONF_MAXSWAP=$((SWAP_MB*2))/" /etc/dphys-swapfile
  else
    echo "CONF_MAXSWAP=$((SWAP_MB*2))" | $SUDO tee -a /etc/dphys-swapfile >/dev/null
  fi
  $SUDO dphys-swapfile setup
  $SUDO dphys-swapfile swapon
  ok "Swap set to ${SWAP_MB} MB."
else
  warn "dphys-swapfile not present — skipping swap config. Add a swapfile manually if runs get OOM-killed."
fi

# ---- 3. Timezone -----------------------------------------------------------
step "Setting timezone to ${TIMEZONE}"
if command -v timedatectl >/dev/null 2>&1; then
  $SUDO timedatectl set-timezone "$TIMEZONE" && ok "Timezone set (daily timer will fire at ${RUN_TIME} local)."
else
  warn "timedatectl unavailable — set the timezone manually so the ${RUN_TIME} timer is correct."
fi

# ---- 4. Secrets template ---------------------------------------------------
step "Creating .env secrets template"
ENV_FILE="$REPO/.env"
if [ -f "$ENV_FILE" ]; then
  ok ".env already exists — left untouched."
else
  umask 077
  cat > "$ENV_FILE" <<EOF
# --- Required ---
LLM_PRIMARY_API_KEY=REPLACE_ME        # primary provider key (Gemini by default)
TELEGRAM_BOT_TOKEN=REPLACE_ME
TELEGRAM_CHAT_ID=REPLACE_ME
# --- Optional fallback provider (served when the primary trips its breaker) ---
LLM_FALLBACK_API_KEY=                  # e.g. a prepaid OpenAI key for gpt-5.4-mini
CV_PHONE=
TELEGRAPH_ACCESS_TOKEN=
# Keep non-secret provider, source, output, and host controls in
# .deployment.env. The private TOML selects the CV and policy files.
EOF
  chown "$REAL_USER" "$ENV_FILE" 2>/dev/null || true
  chmod 600 "$ENV_FILE"
  ok "Wrote $ENV_FILE (mode 600). You MUST fill in the REPLACE_ME values."
fi

# ---- 5. Seed dedup state (avoid the first-run trap) ------------------------
step "Seeding seen_jobs.json from origin/state"
if [ -f "$REPO/seen_jobs.json" ]; then
  ok "seen_jobs.json already present — leaving your existing dedup history."
else
  SEED_TMP="$(mktemp "$REPO/.seen_jobs.seed.XXXXXX")"
  if sudo -u "$REAL_USER" git -C "$REPO" fetch origin state --depth 1 >/dev/null 2>&1 \
     && sudo -u "$REAL_USER" git -C "$REPO" show origin/state:seen_jobs.json > "$SEED_TMP" 2>/dev/null \
     && [ -s "$SEED_TMP" ]; then
    mv "$SEED_TMP" "$REPO/seen_jobs.json"
    chown "$REAL_USER" "$REPO/seen_jobs.json" 2>/dev/null || true
    ok "Seeded seen_jobs.json ($(wc -c < "$REPO/seen_jobs.json") bytes) — first run will behave like a normal day."
  else
    rm -f "$SEED_TMP"
    warn "Couldn't fetch origin/state:seen_jobs.json. The FIRST run will treat every job as new"
    warn "and may take hours. Seed it manually before enabling the timer (see docs/deploy-rpi.md)."
  fi
fi

# ---- 6. Python environment -------------------------------------------------
PY_BIN="/usr/bin/python3"
step "Installing optional TOML and AES ZIP support"
if sudo -u "$REAL_USER" python3 -m venv "$REPO/.venv" \
   && sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q --upgrade pip; then
  PY_BIN="$REPO/.venv/bin/python"
  sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q 'tomli>=2.0,<2.4; python_version < "3.11"' \
    || die "Couldn't install tomli for Python 3.9/3.10 TOML configuration support."
  if sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q 'pyzipper~=0.4.0'; then
    ok "TOML and AES ZIP support installed — hosted CV archives are enabled."
  else
    warn "TOML support installed; pyzipper failed, so Telegraph runs will safely fall back to Telegram ZIP delivery."
  fi
else
  die "Couldn't create the Python environment required for TOML configuration support."
fi

# ---- 6a. Optional: attempt JobSpy (pandas) ---------------------------------
if [ "$TRY_JOBSPY" = "1" ]; then
  step "Attempting python-jobspy in a venv (adds 3 sources; usually fails on ARMv6)"
  if sudo -u "$REAL_USER" python3 -m venv "$REPO/.venv" \
     && sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q python-jobspy; then
    PY_BIN="$REPO/.venv/bin/python"
    ok "python-jobspy installed — the service will use the venv interpreter."
  else
    warn "python-jobspy failed to install (expected on ARMv6: pydantic-core/tls-client have no wheel)."
    warn "No harm — those 3 sources are lazily imported and simply drop out."
  fi
fi

# ---- 6b. Fetch and validate the pinned private configuration ----------------
step "Synchronizing the pinned private configuration"
sudo -u "$REAL_USER" env HOME="$REAL_HOME" bash "$REPO/scripts/prepare-private-config.sh" --sync
sudo -u "$REAL_USER" env HOME="$REAL_HOME" bash "$REPO/scripts/prepare-private-config.sh" --check-only
CONFIG_SETTINGS_FILE="$REPO/.private-config/job-search-config/job_search.toml"
sudo -u "$REAL_USER" env HOME="$REAL_HOME" JOB_SEARCH_SETTINGS_FILE="$CONFIG_SETTINGS_FILE" \
  "$PY_BIN" -m job_search.pipeline --validate-config "$CONFIG_SETTINGS_FILE"
ok "Private configuration is pinned and TOML validation passed."

# ---- 6c. Pre-warm pdflatex -------------------------------------------------
step "Pre-warming pdflatex (builds the format/font cache so the first timed compile isn't cold)"
if command -v pdflatex >/dev/null 2>&1; then
  WARM_DIR="$(mktemp -d)"
  BASE_TEX_FILE="$(sudo -u "$REAL_USER" env HOME="$REAL_HOME" JOB_SEARCH_SETTINGS_FILE="$CONFIG_SETTINGS_FILE" \
    "$PY_BIN" -c 'from job_search.config import PipelineConfig; print(PipelineConfig.from_env().base_tex_file)')"
  if [ -n "$BASE_TEX_FILE" ] \
     && sudo -u "$REAL_USER" env HOME="$REAL_HOME" pdflatex -interaction=nonstopmode \
          -output-directory "$WARM_DIR" "$BASE_TEX_FILE" >/dev/null 2>&1 \
     && find "$WARM_DIR" -maxdepth 1 -name '*.pdf' -print -quit | grep -q .; then
    ok "Configured base CV compiled to PDF — the pdflatex toolchain works."
  else
    warn "Cold pdflatex compile did not produce a PDF from the configured base CV."
  fi
  rm -rf "$WARM_DIR"
else
  warn "pdflatex not on PATH after install — CV PDF generation will fail."
fi

# ---- 6d. Run wrapper + logs directory --------------------------------------
# Every run (the timer, the bot's /run and /tailor) goes through this one
# flock'd wrapper so the single core never runs two pipelines at once.
step "Preparing the run wrapper and logs directory"
chmod +x "$REPO/scripts/run_pipeline.sh"
chmod +x "$REPO/scripts/prepare-private-config.sh"
mkdir -p "$REPO/logs"
chown "$REAL_USER" "$REPO/logs" 2>/dev/null || true
ok "run_pipeline.sh is executable; logs/ ready."

# ---- 7. systemd service + timer + control bot ------------------------------
step "Writing systemd services (daily run + control bot) + timer"
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=AI Job Hunter daily run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${REAL_USER}
WorkingDirectory=${REPO}
EnvironmentFile=${DEPLOYMENT_ENV}
EnvironmentFile=${ENV_FILE}
ExecStartPre=${REPO}/scripts/prepare-private-config.sh --check-only
ExecStart=/usr/bin/env JOB_SEARCH_SETTINGS_FILE=${REPO}/.private-config/job-search-config/job_search.toml ${REPO}/scripts/run_pipeline.sh timer
Nice=10
TimeoutStartSec=7200
EOF

# Telegram control bot: long-polls getUpdates (all outbound HTTPS — works behind
# NAT with no port forwarding) and triggers runs through the same wrapper.
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}-bot.service" >/dev/null <<EOF
[Unit]
Description=AI Job Hunter Telegram control bot (/run, /status, /tailor)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=30
User=${REAL_USER}
WorkingDirectory=${REPO}
EnvironmentFile=${DEPLOYMENT_ENV}
EnvironmentFile=${ENV_FILE}
ExecStartPre=${REPO}/scripts/prepare-private-config.sh --check-only
ExecStart=/usr/bin/env JOB_SEARCH_SETTINGS_FILE=${REPO}/.private-config/job-search-config/job_search.toml ${PY_BIN} -m job_search.bot
Nice=10

[Install]
WantedBy=multi-user.target
EOF

$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.timer" >/dev/null <<EOF
[Unit]
Description=Run AI Job Hunter every morning

[Timer]
OnCalendar=*-*-* ${RUN_TIME}:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

$SUDO systemctl daemon-reload
ok "Wrote ${SERVICE_NAME}.service, ${SERVICE_NAME}-bot.service, and ${SERVICE_NAME}.timer without changing their state."

# ---- Done: next steps ------------------------------------------------------
echo
echo "Operate it:"
echo "  Setup preserved the existing timer and bot states."
echo "  Inspect:   systemctl is-enabled ${SERVICE_NAME}.timer; systemctl is-enabled ${SERVICE_NAME}-bot.service"
echo "  Run now:   $SUDO systemctl start ${SERVICE_NAME}.service"
echo "  Logs:      journalctl -u ${SERVICE_NAME}.service -f"
echo "  Bot logs:  journalctl -u ${SERVICE_NAME}-bot.service -f"
echo "  Disable:   $SUDO systemctl disable --now ${SERVICE_NAME}.timer"
echo "  Control:   from the authorized Telegram chat — /run, /status, /tailor"
echo
echo "Optional — share dedup state with the GitHub Actions runner (staggered runs"
echo "inherit each other's seen jobs, so neither re-delivers a posting):"
echo "  bash $REPO/scripts/setup-state-sync.sh   # then set STATE_SYNC=1 in .env"
echo
ok "Setup complete."
