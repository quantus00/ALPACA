#!/usr/bin/env bash
# BACKTEST: replay a candle CSV through the ICC strategy. No account/keys needed.
#   scripts/backtest.sh --ltf-csv "COINBASE_BTCUSD, 15.csv" --htf-seconds 3600
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate
exec icc-backtest "$@"
