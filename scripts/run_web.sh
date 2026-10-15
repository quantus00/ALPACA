#!/usr/bin/env bash
# ICC Cockpit — browser control surface (backtest, bot, positions, orders).
# Open http://<droplet-ip>:8787 in a browser (forward the port in VS Code if remote).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate
export COINBASE_KEY_FILE="${COINBASE_KEY_FILE:-$HOME/cdp_api_key.json}"
export ICC_WEB_PORT="${ICC_WEB_PORT:-8787}"
# Optional: set COCKPIT_TOKEN=... to require ?token=... on every request.
if [ -z "${COCKPIT_TOKEN:-}" ]; then
  echo "WARNING: COCKPIT_TOKEN not set — cockpit API is unauthenticated. Keep the port private."
fi
echo "ICC cockpit -> http://0.0.0.0:${ICC_WEB_PORT}  (Ctrl-C to stop)"
exec icc-cockpit
