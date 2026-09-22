"""Alex bot configuration — asset-agnostic, cockpit-routed.

Defaults to MES futures on Webull, live (per request). Any symbol/instrument
that the cockpit's Webull or Coinbase connections support can be traded by
overriding the fields below or the matching CLI flags / env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class BotConfig:
    # ---- what to trade (defaults: MES futures, Webull, live) ----------------
    broker: str = os.getenv("ALEX_BROKER", "webull")        # webull | coinbase
    instrument: str = os.getenv("ALEX_INSTRUMENT", "futures")  # futures|crypto|stock|option
    symbol: str = os.getenv("ALEX_SYMBOL", "MES")           # e.g. MES, BTC-USD, SPY, TSLA
    account_id: str = os.getenv("ALEX_ACCOUNT_ID", "")      # cockpit account to route to
    contract_size: float = _envf("ALEX_SIZE", 1.0)          # contracts / units / coins

    # ---- data timeframes ----------------------------------------------------
    entry_tf: str = os.getenv("ALEX_ENTRY_TF", "15m")
    structure_tf: str = os.getenv("ALEX_STRUCTURE_TF", "1h")

    # ---- execution ----------------------------------------------------------
    # mode is the account the cockpit trades against ("paper" | "live").
    # Default is live per request, BUT a live order only transmits when the
    # single master switch `live_armed` is on (set by --live). No per-order gate.
    mode: str = os.getenv("ALEX_MODE", "live")
    live_armed: bool = os.getenv("ALEX_LIVE", "0").strip().lower() in ("1", "true", "yes", "on")

    # ---- risk ---------------------------------------------------------------
    equity: float = _envf("ALEX_EQUITY", 10000.0)
    risk_pct: float = _envf("ALEX_RISK", 1.0)
    poll_seconds: int = int(_envf("ALEX_POLL", 300))

    def dry_run(self) -> bool:
        """True = simulate, never send a real order. Live requires mode==live
        AND the master switch armed."""
        return not (self.mode == "live" and self.live_armed)

    def describe(self) -> str:
        gate = "LIVE-ARMED" if not self.dry_run() else "paper/dry-run"
        return (f"{self.broker}:{self.instrument}:{self.symbol} size={self.contract_size} "
                f"mode={self.mode} [{gate}] entry={self.entry_tf} structure={self.structure_tf}")
