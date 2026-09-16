#!/usr/bin/env bash
# PAPER: live Coinbase data, simulated fills + P&L. No real orders. Safe.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate

export COINBASE_KEY_FILE="${COINBASE_KEY_FILE:-$HOME/cdp_api_key.json}"
export ICC_BROKER="${ICC_BROKER:-coinbase}"
export ICC_VENUE="${ICC_VENUE:-futures}"
export ICC_SYMBOLS="${ICC_SYMBOLS:-BIT,GOL,NOL}"
export ICC_MODE=paper
export ICC_SESSION_ENABLED="${ICC_SESSION_ENABLED:-false}"
export ICC_PAPER_EQUITY="${ICC_PAPER_EQUITY:-10000}"
export ICC_RISK_PCT="${ICC_RISK_PCT:-1.0}"

echo "PAPER mode — simulated fills, no real orders. Symbols: $ICC_SYMBOLS"
exec icc-bot "$@"   # pass --once for a single cycle
