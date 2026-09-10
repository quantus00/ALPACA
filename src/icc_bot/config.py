"""Configuration for an ICC bot instance, assembled from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Mode
from .risk import RiskLimits
from .strategy import ICCParams

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class BotConfig:
    broker: str = "coinbase"                 # "coinbase" or "webull"
    mode: Mode = Mode.DRY_RUN
    symbols: List[str] = field(default_factory=lambda: ["BTC-USD"])
    htf: str = "1h"                          # structure timeframe
    ltf: str = "15m"                         # entry timeframe
    poll_seconds: int = 60
    # Session filter (UTC hours, 24h). Default ~NY open 9:30–11:30 ET.
    session_start_utc: int = 13
    session_end_utc: int = 16
    session_enabled: bool = True
    params: ICCParams = field(default_factory=ICCParams)
    risk: RiskLimits = field(default_factory=RiskLimits)

    @property
    def live_confirmed(self) -> bool:
        """Live trading requires an explicit, unambiguous opt-in env var."""
        return os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") == "yes"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def load_config() -> BotConfig:
    broker = _env("ICC_BROKER", "coinbase").lower()
    mode = Mode(_env("ICC_MODE", "dry_run").lower())
    symbols = [s.strip() for s in _env(
        "ICC_SYMBOLS", "BTC-USD" if broker == "coinbase" else "QQQ"
    ).split(",") if s.strip()]

    cfg = BotConfig(
        broker=broker,
        mode=mode,
        symbols=symbols,
        htf=_env("ICC_HTF", "1h"),
        ltf=_env("ICC_LTF", "15m"),
        poll_seconds=int(_env("ICC_POLL_SECONDS", "60")),
        session_start_utc=int(_env("ICC_SESSION_START_UTC", "13")),
        session_end_utc=int(_env("ICC_SESSION_END_UTC", "16")),
        session_enabled=_env("ICC_SESSION_ENABLED", "true").lower() in _TRUTHY,
        params=ICCParams(
            # Pivot length ~5 matches the trader's "Pivot Points High Low"
            # setting ("crank it down to five", course 2 day 1).
            htf_lookback=int(_env("ICC_HTF_LOOKBACK", "5")),
            ltf_lookback=int(_env("ICC_LTF_LOOKBACK", "3")),
            target_rr=float(_env("ICC_TARGET_RR", "3.0")),  # course: 1:3 to 1:4
        ),
        risk=RiskLimits(
            risk_per_trade_pct=float(_env("ICC_RISK_PCT", "1.0")),
            max_daily_loss_pct=float(_env("ICC_MAX_DAILY_LOSS_PCT", "3.0")),
            max_open_positions=int(_env("ICC_MAX_OPEN", "1")),
            # Course emphasizes 1–2 trades/week; keep the daily throttle tight.
            max_trades_per_day=int(_env("ICC_MAX_TRADES_PER_DAY", "2")),
            max_position_pct=float(_env("ICC_MAX_POSITION_PCT", "25.0")),
        ),
    )
    return cfg
