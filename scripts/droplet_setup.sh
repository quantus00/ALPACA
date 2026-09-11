#!/usr/bin/env bash
# One-shot setup for the ICC bot on a fresh droplet (Ubuntu/Debian).
# Safe to re-run. Does NOT place any trades — leaves you in dry-run.
set -euo pipefail

REPO="${REPO:-https://github.com/quantus00/ALPACA.git}"
BRANCH="${BRANCH:-claude/mcp-server-configuration-6exeft}"
DIR="${DIR:-$HOME/ALPACA}"

echo "==> Ensuring python3-venv + git"
sudo apt-get update -y >/dev/null 2>&1 || true
sudo apt-get install -y python3-venv git >/dev/null 2>&1 || true

echo "==> Fetching $REPO ($BRANCH) into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout "$BRANCH" && git -C "$DIR" pull --ff-only origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

echo "==> Creating venv + installing (with Coinbase SDK)"
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e '.[coinbase]'

echo
echo "==> Setup complete. Next steps:"
echo "   1) Put your CDP key JSON somewhere and point to it:"
echo "        export COINBASE_KEY_FILE=\$HOME/cdp_api_key.json"
echo "   2) Discover your live products / INTX portfolio:"
echo "        . $DIR/.venv/bin/activate && icc-discover"
echo "   3) Dry-run the bot (places NOTHING) using bare roots that auto-roll:"
echo "        ICC_BROKER=coinbase ICC_VENUE=futures ICC_SYMBOLS=BIT,GOL \\"
echo "          ICC_MODE=dry_run icc-bot"
echo "   4) Perps (needs INTX): ICC_VENUE=perp ICC_SYMBOLS=BTC-PERP ..."
echo
echo "   Live trading requires BOTH: ICC_MODE=live and"
echo "   ICC_I_UNDERSTAND_LIVE_RISK=yes  (leverage magnifies losses)."
