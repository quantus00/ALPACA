#!/usr/bin/env bash
# Web cockpit for ICC backtests. Open http://<droplet-ip>:8787 in a browser.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate
export COINBASE_KEY_FILE="${COINBASE_KEY_FILE:-$HOME/cdp_api_key.json}"
export ICC_WEB_PORT="${ICC_WEB_PORT:-8787}"
echo "ICC backtest cockpit -> http://0.0.0.0:${ICC_WEB_PORT}  (Ctrl-C to stop)"
exec icc-web
