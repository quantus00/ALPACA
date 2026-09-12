#!/usr/bin/env bash
# LIVE: real orders with real money on Coinbase. Leverage magnifies losses.
# Requires ICC_I_UNDERSTAND_LIVE_RISK=yes in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate

export COINBASE_KEY_FILE="${COINBASE_KEY_FILE:-$HOME/cdp_api_key.json}"
export ICC_BROKER="${ICC_BROKER:-coinbase}"
export ICC_VENUE="${ICC_VENUE:-futures}"
export ICC_SYMBOLS="${ICC_SYMBOLS:-BIT,GOL,NOL}"
export ICC_MODE=live
export ICC_SESSION_ENABLED="${ICC_SESSION_ENABLED:-false}"
export ICC_RISK_PCT="${ICC_RISK_PCT:-0.5}"   # conservative default

if [ "${ICC_I_UNDERSTAND_LIVE_RISK:-}" != "yes" ]; then
  echo "REFUSING to start LIVE: set ICC_I_UNDERSTAND_LIVE_RISK=yes to confirm." >&2
  echo "  e.g.  ICC_I_UNDERSTAND_LIVE_RISK=yes ICC_RISK_PCT=0.5 scripts/run_live.sh" >&2
  exit 1
fi

echo "*** LIVE mode — REAL MONEY. Symbols: $ICC_SYMBOLS  risk/trade: ${ICC_RISK_PCT}% ***"
exec icc-bot "$@"
