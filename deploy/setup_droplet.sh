#!/usr/bin/env bash
# ============================================================================
# One-time droplet setup for the ALPACA trading bots + Claude Code.
# Idempotent: safe to re-run after a `git pull`.
#
#   sudo bash deploy/setup_droplet.sh
#
# Afterwards:
#   1. sudo nano /etc/alpaca/alpaca.env       # paste your keys/token
#   2. sudo systemctl enable --now trend-bot.service
#   3. sudo systemctl enable --now spy-0dte.timer
# ============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/alpaca}"
APP_USER="${APP_USER:-trader}"
ENV_DIR="/etc/alpaca"
ENV_FILE="$ENV_DIR/alpaca.env"
TZ_NAME="${TZ_NAME:-America/New_York}"
REPO_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates

log "Setting timezone to $TZ_NAME (the 0DTE timer assumes ET)"
timedatectl set-timezone "$TZ_NAME" || echo "  (could not set timezone; set it manually)"

log "Ensuring service user '$APP_USER'"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"

log "Syncing code into $APP_DIR"
mkdir -p "$APP_DIR"
if [ "$REPO_SRC" != "$APP_DIR" ]; then
  # copy everything except venv/git noise
  tar -C "$REPO_SRC" --exclude=.git --exclude=.venv --exclude=__pycache__ -cf - . \
    | tar -C "$APP_DIR" -xf -
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "Creating Python venv + installing requirements"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" || true
# Broker SDKs the scripts import lazily (requirements.txt keeps them commented).
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet requests coinbase-advanced-py

log "Installing Node + Claude Code CLI"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs
fi
npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || \
  echo "  (claude-code install failed — run: sudo npm i -g @anthropic-ai/claude-code)"

log "Preparing $ENV_FILE"
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/deploy/alpaca.env.example" "$ENV_FILE"
  echo "  created from template — fill it in"
else
  echo "  already exists — left untouched"
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

log "Installing systemd units"
install -m 644 "$APP_DIR"/deploy/trend-bot.service /etc/systemd/system/
install -m 644 "$APP_DIR"/deploy/spy-0dte.service  /etc/systemd/system/
install -m 644 "$APP_DIR"/deploy/spy-0dte.timer    /etc/systemd/system/
systemctl daemon-reload

cat <<DONE

============================================================
 Setup complete.

 1) Put your keys/token in:   sudo nano $ENV_FILE
 2) Log Claude Code in as '$APP_USER':
       sudo -u $APP_USER -i
       claude            # then /login, paste the code from your browser
    ...or just set CLAUDE_CODE_OAUTH_TOKEN in $ENV_FILE
    (get it by running 'claude setup-token' on a machine with a browser).
 3) Start things:
       sudo systemctl enable --now trend-bot.service
       sudo systemctl enable --now spy-0dte.timer
 4) Watch them:
       systemctl status trend-bot
       journalctl -u trend-bot -f
       systemctl list-timers spy-0dte.timer

 NOTE: bots default to PAPER/dry-run. Going live is an explicit
 change in $ENV_FILE (ALPACA_BASE_URL / BOT_DRY_RUN) plus flags.
============================================================
DONE
