"""Trader cockpit config. Secrets come ONLY from environment variables.

Ported from the Drive cockpit, with the bot/backtest presets pointed at the
ICC engine (`python -m icc_bot` / `icc-backtest`).
"""
from __future__ import annotations

import os

# Shared token so only you can use the app on your LAN. Set COCKPIT_TOKEN; if
# unset the app runs OPEN and warns (keep the port private then).
ACCESS_TOKEN = os.environ.get("COCKPIT_TOKEN")

DEFAULT_MODE = "paper"

# LIVE accounts that must NEVER be traded from the app without a deliberate
# override. Seeded with the user's real Webull live accounts.
OFF_LIMITS = {
    "03II4F2VGEVM20I38F6EIPCBRA",   # Webull Individual Cash (LIVE)
    "A8GM4URJ1B1BCHCE67K5E8OQEB",   # Webull Individual Margin (LIVE)
    "co727816c01",                  # Webull Crypto Cash (LIVE)
    "DEH69P5U1IJN57JTKDB743PSAA",   # Webull Futures (LIVE)
}

# Which brokers the dashboard exposes. 'mock' always works (no keys/net).
BROKERS = ["mock", "coinbase", "webull"]

# Bots the dashboard can start/stop. Each value is either a command list, or a
# dict {"cmd": [...], "env": {...}} to pass ICC settings per bot. All default to
# ICC_MODE=dry_run so nothing trades until you deliberately change it.
def _icc(venue: str, symbols: str, extra: dict | None = None) -> dict:
    env = {"ICC_BROKER": "coinbase", "ICC_VENUE": venue,
           "ICC_SYMBOLS": symbols, "ICC_MODE": "dry_run"}
    env.update(extra or {})
    return {"cmd": ["python", "-m", "icc_bot"], "env": env}


BOTS = {
    "ICC — BTC spot (Coinbase)":        _icc("spot", "BTC-USD"),
    "ICC — ETH spot (Coinbase)":        _icc("spot", "ETH-USD"),
    "ICC — BTC futures front-month":    _icc("futures", "BIT"),
    "ICC — Gold futures (GOL)":         _icc("futures", "GOL"),
    "ICC — BTC perp / nano":            _icc("perp", "BTC-PERP"),
}

# Backtesters the dashboard can run (name -> command; args appended by the UI).
# `icc-backtest --fetch` pulls candles from Coinbase (needs COINBASE_KEY_FILE).
BACKTESTS = {
    "ICC — BTC 15m/1h (60d)": ["icc-backtest", "--fetch", "--symbol", "BTC-USD",
                               "--ltf", "15m", "--days", "60", "--htf-seconds", "3600"],
    "ICC — ETH 15m/1h (60d)": ["icc-backtest", "--fetch", "--symbol", "ETH-USD",
                               "--ltf", "15m", "--days", "60", "--htf-seconds", "3600"],
}

# repo root = four levels up (src/icc_bot/trader/config.py -> repo). Bot and
# backtest commands run from here.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
HERE = os.path.dirname(os.path.abspath(__file__))

# Keep ACCESS_TOKEN in sync with the environment at import time.
ACCESS_TOKEN = os.environ.get("COCKPIT_TOKEN")
