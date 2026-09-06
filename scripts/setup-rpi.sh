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
#   4. Creates a .env secrets template (never overwrites an existing one)
#   5. Seeds seen_jobs.json from the origin/state branch (avoids the first-run trap)
#   6. Pre-warms pdflatex so the first timed compile isn't the cold one
#   7. Installs a systemd service + timer for the daily run
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
# --- Optional provider overrides (blank/unset = application defaults) ---
# A provider = scheme (gemini | openai | anthropic) + model + key (+ optional base).
# Switching providers is a config edit only — no code change.
# Primary defaults:  scheme=gemini  model=gemini-2.5-flash
# LLM_PRIMARY_SCHEME=gemini
# LLM_PRIMARY_MODEL=gemini-2.5-flash
# LLM_PRIMARY_API_BASE=
# LLM_PRIMARY_AUTH_MODE=bearer
# Fallback defaults: scheme=openai  model=gpt-5.4-mini
# LLM_FALLBACK_SCHEME=openai
# LLM_FALLBACK_MODEL=gpt-5.4-mini
# LLM_FALLBACK_API_BASE=
# LLM_FALLBACK_AUTH_MODE=bearer
# Worked examples (openai scheme covers any OpenAI-compatible endpoint via api_base):
#   Groq:      LLM_FALLBACK_SCHEME=openai     LLM_FALLBACK_API_BASE=https://api.groq.com/openai/v1
#   xAI Grok:  LLM_FALLBACK_SCHEME=openai     LLM_FALLBACK_API_BASE=https://api.x.ai/v1     (e.g. grok-4.3)
#   Anthropic: LLM_FALLBACK_SCHEME=anthropic  LLM_FALLBACK_MODEL=claude-haiku-4-5
# Local OpenAI-compatible server (for example LM Studio on this Pi/host):
# LLM_PRIMARY_SCHEME=openai
# LLM_PRIMARY_MODEL=your-loaded-model-id
# LLM_PRIMARY_API_BASE=http://127.0.0.1:1234/v1
# LLM_PRIMARY_AUTH_MODE=none
# --- Optional output / prompts / LaTeX overrides (blank = current Telegram behavior) ---
# OUTPUT_MODE=telegram        # telegram (default) | html | plain
# OUTPUT_DIR=                 # filesystem destination for html/plain modes
# OUTPUT_CV_MODE=required     # required (default) | disabled; telegram requires required
# PROMPT_DIR=                 # directory of file-backed prompt overrides
# PROMPT_REVISION=            # required whenever PROMPT_DIR is set
# LATEX_ENGINE=pdflatex       # e.g. xelatex
# --- Optional escape hatch: job_search_config.py, trusted executable Python for
#     the rare thing that genuinely needs code (unvalidated; a mistake in it
#     surfaces as that file's own traceback). Missing/unset uses an optional
#     job_search_config.py in this repo, then built-ins. An explicitly-set-but-
#     empty value is an error, not "disabled". Keep secrets here in .env, never
#     inside that module. ---
# JOB_SEARCH_CONFIG_FILE=/absolute/path/to/job_search_config.py
# --- Pi tuning (single core / 512 MB) ---
EVAL_WORKERS=${EVAL_WORKERS}
TAILOR_WORKERS=${TAILOR_WORKERS}
SCRAPE_BUDGET_SECONDS=${SCRAPE_BUDGET_SECONDS}
# --- Sources: run the stdlib LinkedIn guest source (works on ARMv6) instead of
#     the jobspy-backed LinkedIn sources it supersedes. ---
SOURCES_ENABLE=linkedin-guest
SOURCES_DISABLE=linkedin-global,linkedin-israel
# --- Dedup-state sync with the orphan 'state' branch. 0 = off; set to 1 AFTER
#     running scripts/setup-state-sync.sh (creates the .state checkout + deploy key). ---
STATE_SYNC=0
EOF
  chown "$REAL_USER" "$ENV_FILE" 2>/dev/null || true
  chmod 600 "$ENV_FILE"
  ok "Wrote $ENV_FILE (mode 600). You MUST fill in the REPLACE_ME values."
fi

# ---- 5. Seed dedup state (avoid the first-run trap) ------------------------
step "Seeding seen_jobs.json from origin/state"
if [ -f "$REPO/seen_jobs.json" ]; then
  ok "seen_jobs.json already present — leaving your existing dedup history."
elif sudo -u "$REAL_USER" git -C "$REPO" fetch origin state --depth 1 >/dev/null 2>&1 \
     && sudo -u "$REAL_USER" git -C "$REPO" show origin/state:seen_jobs.json > "$REPO/seen_jobs.json" 2>/dev/null \
     && [ -s "$REPO/seen_jobs.json" ]; then
  chown "$REAL_USER" "$REPO/seen_jobs.json" 2>/dev/null || true
  ok "Seeded seen_jobs.json ($(wc -c < "$REPO/seen_jobs.json") bytes) — first run will behave like a normal day."
else
  rm -f "$REPO/seen_jobs.json"
  warn "Couldn't fetch origin/state:seen_jobs.json. The FIRST run will treat every job as new"
  warn "and may take hours. Seed it manually before enabling the timer (see docs/deploy-rpi.md)."
fi

# ---- 6. Pre-warm pdflatex --------------------------------------------------
step "Pre-warming pdflatex (builds the format/font cache so the first timed compile isn't cold)"
if command -v pdflatex >/dev/null 2>&1; then
  WARM_DIR="$(mktemp -d)"
  if sudo -u "$REAL_USER" env HOME="$REAL_HOME" pdflatex -interaction=nonstopmode \
        -output-directory "$WARM_DIR" "$REPO/igor_pivnyk_cv_base_updated.tex" >/dev/null 2>&1 \
        && [ -f "$WARM_DIR/igor_pivnyk_cv_base_updated.pdf" ]; then
    ok "Base CV compiled to PDF — the pdflatex toolchain works."
  else
    warn "Cold pdflatex compile did not produce a PDF. Run it by hand to see the error:"
    warn "  cd $REPO && pdflatex igor_pivnyk_cv_base_updated.tex"
  fi
  rm -rf "$WARM_DIR"
else
  warn "pdflatex not on PATH after install — CV PDF generation will fail."
fi

# ---- 7. Python environment -------------------------------------------------
PY_BIN="/usr/bin/python3"
step "Installing AES ZIP support"
if sudo -u "$REAL_USER" python3 -m venv "$REPO/.venv" \
   && sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q --upgrade pip \
   && sudo -u "$REAL_USER" "$REPO/.venv/bin/pip" install -q 'pyzipper~=0.4.0'; then
  PY_BIN="$REPO/.venv/bin/python"
  ok "AES ZIP support installed — hosted CV archives are enabled."
else
  warn "pyzipper failed to install; Telegraph runs will safely fall back to Telegram ZIP delivery."
fi

# ---- 7a. Optional: attempt JobSpy (pandas) ---------------------------------
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

# ---- 7b. Run wrapper + logs directory --------------------------------------
# Every run (the timer, the bot's /run and /tailor) goes through this one
# flock'd wrapper so the single core never runs two pipelines at once.
step "Preparing the run wrapper and logs directory"
chmod +x "$REPO/scripts/run_pipeline.sh"
mkdir -p "$REPO/logs"
chown "$REAL_USER" "$REPO/logs" 2>/dev/null || true
ok "run_pipeline.sh is executable; logs/ ready."

# ---- 8. systemd service + timer + control bot ------------------------------
step "Installing systemd services (daily run + control bot) + timer"
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=AI Job Hunter daily run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${REAL_USER}
WorkingDirectory=${REPO}
EnvironmentFile=${ENV_FILE}
ExecStart=${REPO}/scripts/run_pipeline.sh timer
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
EnvironmentFile=${ENV_FILE}
ExecStart=${PY_BIN} -m job_search.bot
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
ok "Installed ${SERVICE_NAME}.service, ${SERVICE_NAME}-bot.service, and ${SERVICE_NAME}.timer."

# ---- Done: next steps ------------------------------------------------------
echo
if grep -q 'REPLACE_ME' "$ENV_FILE"; then
  step "ALMOST DONE — finish these steps:"
  echo -e "  ${c_yellow}1.${c_off} Put your real secrets in the .env file:"
  echo    "        nano $ENV_FILE"
  echo -e "  ${c_yellow}2.${c_off} Smoke-test the full path (fetch → tailor → PDF → Telegram):"
  echo    "        cd $REPO && set -a && . ./.env && set +a"
  echo    "        $PY_BIN -m job_search.pipeline --tailor \\"
  echo    "          --job-text 'Senior iOS Engineer, remote, Swift/SwiftUI' \\"
  echo    "          --title 'Senior iOS Developer' --company 'Acme'"
  echo -e "  ${c_yellow}3.${c_off} Enable the daily timer (fires at ${RUN_TIME} ${TIMEZONE}):"
  echo    "        $SUDO systemctl enable --now ${SERVICE_NAME}.timer"
  echo -e "  ${c_yellow}4.${c_off} Enable the Telegram control bot (also after step 1):"
  echo    "        $SUDO systemctl enable --now ${SERVICE_NAME}-bot.service"
  warn "Timer & bot NOT enabled yet — do it after step 1, or they fail on the placeholder token."
else
  step "Enabling the daily timer (fires at ${RUN_TIME} ${TIMEZONE}) + control bot"
  $SUDO systemctl enable --now "${SERVICE_NAME}.timer"
  $SUDO systemctl enable --now "${SERVICE_NAME}-bot.service"
  ok "Timer and control bot enabled."
  echo    "  Next scheduled run:   systemctl list-timers ${SERVICE_NAME}.timer"
fi
echo
echo "Operate it:"
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
