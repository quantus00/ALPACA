"""Load ``hedge_config.toml`` into typed settings for one pair (crypto / forex)."""
from __future__ import annotations

import os
try:
    import tomllib                 # Python 3.11+
except ModuleNotFoundError:        # pragma: no cover - Python 3.9/3.10: pip install tomli
    import tomli as tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("hedge_config.toml")


@dataclass
class PairSettings:
    pair: str                      # "crypto" | "forex"
    primary_symbol: str
    hedge_symbol: str
    hedge_ratio: float

    # account
    equity_usd: float
    primary_notional_usd: float

    # trigger
    atr_length: int
    atr_mult: float
    atr_reference: str
    use_correction_break: bool
    pivot_lookback: int

    # lift / cut
    require_continuation: bool
    require_reclaim: bool
    require_continuation_1h_trend: bool
    trail_atr_mult: float

    # limits
    max_combined_loss_pct: float
    max_hedge_bars: int            # 1h bars; crypto = hours, forex = trading days x 24
    max_funding_pct: float

    # costs
    primary_fee_pct: float = 0.0   # crypto: % of notional per side
    hedge_fee_pct: float = 0.0
    primary_cost_pips: float = 0.0 # forex: pips per side
    hedge_cost_pips: float = 0.0
    funding_interval_hours: int = 1
    default_funding_rate_pct: float = 0.0

    # forex correlation gate
    corr_gate_enabled: bool = False
    corr_threshold: float = -0.80
    corr_window_days: int = 30
    corr_min_obs: int = 200

    # safety
    live_trading_approved: bool = False
    paper_log_path: str = "logs/hedge_events.jsonl"
    max_hedge_btc: float = 0.25
    hedge_contract_btc: float = 0.0
    hedge_size_increment: float = 0.0001

    @property
    def is_crypto(self) -> bool:
        return self.pair == "crypto"


def load_raw(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.getenv("BOT_HEDGE_CONFIG") or DEFAULT_PATH)
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load(pair: str, path: str | os.PathLike | None = None) -> PairSettings:
    raw = load_raw(path)
    if pair not in ("crypto", "forex"):
        raise ValueError(f"pair must be 'crypto' or 'forex', got {pair!r}")
    acc, trg, lift, cut, lim = (raw["account"], raw["trigger"], raw["lift"],
                                raw["cut"], raw["limits"])
    common = dict(
        pair=pair,
        equity_usd=float(acc["equity_usd"]),
        primary_notional_usd=float(acc["primary_notional_usd"]),
        atr_length=int(trg["atr_length"]),
        atr_mult=float(trg["atr_mult"]),
        atr_reference=str(trg["atr_reference"]),
        use_correction_break=bool(trg["use_correction_break"]),
        pivot_lookback=int(trg["pivot_lookback"]),
        require_continuation=bool(lift["require_continuation"]),
        require_reclaim=bool(lift["require_reclaim"]),
        require_continuation_1h_trend=bool(lift["continuation_requires_1h_trend"]),
        trail_atr_mult=float(cut["trail_atr_mult"]),
        max_combined_loss_pct=float(lim["max_combined_loss_pct"]),
        max_funding_pct=float(lim["max_funding_pct_of_notional"]),
        live_trading_approved=bool(raw["safety"]["live_trading_approved"]),
        paper_log_path=str(raw["safety"]["paper_log_path"]),
        max_hedge_btc=float(raw["safety"].get("max_hedge_btc", 0.25)),
        hedge_contract_btc=float(raw["safety"].get("hedge_contract_btc", 0.0)),
        hedge_size_increment=float(raw["safety"].get("hedge_size_increment", 0.0001)),
    )
    if pair == "crypto":
        c = raw["crypto"]
        return PairSettings(
            **common,
            primary_symbol=c["primary_symbol"],
            hedge_symbol=c["hedge_symbol"],
            hedge_ratio=float(c["hedge_ratio"]),
            max_hedge_bars=int(lim["max_hedge_hours_crypto"]),
            primary_fee_pct=float(c["spot_fee_pct"]),
            hedge_fee_pct=float(c["perp_fee_pct"]),
            funding_interval_hours=int(c["funding_interval_hours"]),
            default_funding_rate_pct=float(c["default_funding_rate_pct"]),
        )
    f = raw["forex"]
    return PairSettings(
        **common,
        primary_symbol=f["primary_symbol"],
        hedge_symbol=f["hedge_symbol"],
        hedge_ratio=float(f["hedge_ratio"]),
        max_hedge_bars=int(lim["max_hedge_trading_days_forex"]) * 24,
        primary_cost_pips=float(f["eurusd_cost_pips"]),
        hedge_cost_pips=float(f["usdchf_cost_pips"]),
        corr_gate_enabled=bool(f["corr_gate_enabled"]),
        corr_threshold=float(f["corr_threshold"]),
        corr_window_days=int(f["corr_window_days"]),
        corr_min_obs=int(f["corr_min_obs"]),
    )
