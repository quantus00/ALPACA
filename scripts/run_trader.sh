#!/usr/bin/env bash
# ICC Trader cockpit — the mobile-style dashboard (Accounts / Trade / Bots /
# Backtest / Files), wired to the ICC engine. Runs alongside the plain
# `icc-cockpit` (this one defaults to port 8788).
# Open http://<droplet-ip>:8788/?token=YOURTOKEN
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate
export COINBASE_KEY_FILE="${COINBASE_KEY_FILE:-$HOME/cdp_api_key.json}"
export ICC_TRADER_PORT="${ICC_TRADER_PORT:-8788}"
if [ -z "${COCKPIT_TOKEN:-}" ]; then
  echo "WARNING: COCKPIT_TOKEN not set — Trader cockpit is OPEN. Keep the port private."
fi
echo "ICC Trader cockpit -> http://0.0.0.0:${ICC_TRADER_PORT}/?token=\$COCKPIT_TOKEN  (Ctrl-C to stop)"
exec icc-trader
