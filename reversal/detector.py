"""Uptrend -> downtrend reversal detector (short side only).

What it does, bar by bar (each bar is evaluated only once it has closed):

1. STRUCTURE (always on). Confirmed swing pivots classify the trend exactly
   like ``bot/trend.py``: UP = higher high + higher low. While the trend is UP
   and the market has printed a *lower high* after its top, a close below the
   last swing low (the higher low) is a "break".
2. Break-bar filters: RSI divergence at the top, volume spike on the break.
3. Entry: on the break bar, or (``retest`` on) on a later bar that rallies back
   to the broken level and closes back below it. Entry-bar filters: MACD,
   ADX/DI, higher-timeframe close below its EMA.
4. Exit (first that fires): initial stop above the lower high, hard % stop,
   ATR trailing stop, "early warning" (close back above the broken level with a
   bullish MACD cross), or trend flip back up (higher low, then close above the
   last swing high).

One short per reversal: a top that already produced a trade can't produce
another. Pivots are only used ``pivot_lookback`` bars after they print, so
there is no lookahead — ``tests/test_reversal.py`` checks this.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind

LAYERS = ("divergence", "volume", "retest", "macd", "adx", "htf")
TF_HOURS = {"1h": 1, "4h": 4, "1d": 24, "1w": 168}
CONFIG_PATH = Path(__file__).with_name("config.toml")


def load_config(path: str | Path | None = None) -> dict:
    with open(path or CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def position_size(cfg: dict) -> tuple[float, float, bool]:
    """(BTC exposure per signal, fee $ per BTC per side, product pays funding?)."""
    p = cfg["products"][cfg["sizing"]["product"]]
    qty = cfg["sizing"]["contracts"] * p["btc_per_contract"]
    return qty, p["fee_usd_per_contract_side"] / p["btc_per_contract"], bool(p["funding"])


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    break_level: float = 0.0
    top: float = 0.0
    bars_held: int = 0
    net_return: float = 0.0          # short return after costs, fraction of notional
    pnl_usd: float = 0.0             # dollar P&L for the configured contracts, after costs
    top_time: pd.Timestamp | None = None   # bar of the swing high the reversal came from
    entry_i: int = 0
    path: list = field(default_factory=list, repr=False)  # per-bar mark-to-market returns


# --------------------------------------------------------------------------- data prep
def prepare(df: pd.DataFrame, tf: str, htf_df: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """Add every indicator column the detector needs. ``df`` index = bar open time (UTC)."""
    d = df.copy()
    c, h, l = d["close"], d["high"], d["low"]
    d["rsi"] = ind.rsi(c, cfg["divergence"]["rsi_length"])
    m = cfg["macd"]
    d["macd"], d["macd_sig"] = ind.macd(c, m["fast"], m["slow"], m["signal"])
    d["atr"] = ind.atr(h, l, c, cfg["exits"]["atr_length"])
    d["adx"], d["pdi"], d["mdi"] = ind.adx(h, l, c, cfg["adx"]["length"])
    d["vol_sma"] = d["volume"].rolling(cfg["volume"]["sma_length"]).mean()

    d["htf_bear"] = True
    if htf_df is not None:
        hcfg, htf = cfg["htf"], cfg["htf"]["map"][tf]
        hh = htf_df[["close"]].copy()
        hh["ema"] = ind.ema(hh["close"], hcfg["ema_length"])
        hh["bear"] = hh["close"] < hh["ema"]
        # A higher-timeframe bar is usable once it has CLOSED.
        hh["avail"] = hh.index + pd.Timedelta(hours=TF_HOURS[htf])
        left = pd.DataFrame({"avail": d.index + pd.Timedelta(hours=TF_HOURS[tf])})
        merged = pd.merge_asof(left, hh[["avail", "bear"]].sort_values("avail"), on="avail")
        d["htf_bear"] = merged["bear"].fillna(False).astype(bool).values
    return d


def _pivots(h: np.ndarray, l: np.ndarray, L: int):
    """Boolean arrays: is_ph[j] / is_pl[j] = bar j is a swing high/low (same rule as bot/trend.py)."""
    n = len(h)
    is_ph = np.zeros(n, bool)
    is_pl = np.zeros(n, bool)
    for j in range(L, n - L):
        if (h[j] > h[j - L:j]).all() and (h[j] >= h[j + 1:j + L + 1]).all():
            is_ph[j] = True
        if (l[j] < l[j - L:j]).all() and (l[j] <= l[j + 1:j + L + 1]).all():
            is_pl[j] = True
    return is_ph, is_pl


def invert(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror prices (x -> -x, high<->low) so the bearish rules detect bullish reversals.

    Every indicator mirrors exactly: RSI -> 100-RSI, MACD -> -MACD, +DI <-> -DI,
    and "close below its EMA" becomes "close above its EMA".
    """
    out = df.copy()
    out["open"], out["close"] = -df["open"], -df["close"]
    out["high"], out["low"] = -df["low"], -df["high"]
    return out


# --------------------------------------------------------------------------- engine
def run(d: pd.DataFrame, cfg: dict, layers: dict | None = None, log=None,
        signals_only: bool = False) -> list[Trade]:
    """Walk the bars and return every short trade. ``layers`` overrides cfg['layers'].

    ``signals_only``: don't hold positions — return every bearish reversal signal
    (entry fields filled, exit fields empty) so accuracy can be scored. Run it on
    ``invert(df)`` to get bullish (bottom) reversals with the identical rules.
    """
    lay = dict(cfg["layers"])
    if layers is not None:
        lay.update(layers)
    L = cfg["structure"]["pivot_lookback"]
    ex, costs = cfg["exits"], cfg["costs"]
    rt = cfg["retest"]

    t = d.index
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    vol, vsma = d["volume"].to_numpy(float), d["vol_sma"].to_numpy(float)
    r, mc, ms = d["rsi"].to_numpy(float), d["macd"].to_numpy(float), d["macd_sig"].to_numpy(float)
    atr_, adx_, pdi, mdi = (d[k].to_numpy(float) for k in ("atr", "adx", "pdi", "mdi"))
    htf_bear = d["htf_bear"].to_numpy(bool)
    is_ph, is_pl = _pivots(h, l, L)

    qty, fee_btc_side, uses_funding = position_size(cfg)
    usd_side = fee_btc_side + costs["slippage_usd_per_btc_side"]
    fund_per_bar = (costs["funding_per_8h"] if uses_funding else 0.0) * (pd.Timedelta(t[1] - t[0]).total_seconds() / 28800) if len(t) > 1 else 0.0

    highs: list[tuple[int, float, float]] = []   # (bar, price, rsi)
    lows: list[tuple[int, float]] = []
    trend = 0
    used_top = -1
    setup = None          # pending break waiting for entry
    pos: Trade | None = None
    stop = 0.0
    low_since = 0.0
    entry_i = 0
    trades: list[Trade] = []

    def note(i, kind, msg):
        if log is not None:
            log.append({"time": t[i], "event": kind, "detail": msg, "close": c[i]})

    def entry_ok(i) -> bool:
        if lay["macd"] and not (mc[i] < ms[i] and mc[i] < 0):
            return False
        if lay["adx"] and not (adx_[i] > cfg["adx"]["threshold"] and mdi[i] > pdi[i]):
            return False
        if lay["htf"] and not htf_bear[i]:
            return False
        return True

    for i in range(len(c)):
        # 1) pivots that become known at this bar (printed L bars ago)
        j = i - L
        if j >= 0:
            if is_ph[j]:
                highs.append((j, h[j], r[j], trend))   # trend in force when this high printed
            if is_pl[j]:
                lows.append((j, l[j]))
            if (is_ph[j] or is_pl[j]) and len(highs) >= 2 and len(lows) >= 2:
                hh, hl = highs[-1][1] > highs[-2][1], lows[-1][1] > lows[-2][1]
                lh, ll = highs[-1][1] < highs[-2][1], lows[-1][1] < lows[-2][1]
                if hh and hl:
                    trend = 1
                elif lh and ll:
                    trend = -1

        # 2) manage an open short
        if pos is not None:
            pos.bars_held += 1
            reason, px = "", 0.0
            if ex["use_trail"]:
                low_since = min(low_since, l[i])
                stop = min(stop, low_since + ex["trail_atr"] * atr_[i - 1])
            if h[i] >= stop:
                reason, px = "stop", max(o[i], stop)
            elif ex["use_early_warning"] and c[i] > pos.break_level and mc[i] > ms[i] and mc[i - 1] <= ms[i - 1]:
                reason, px = "early_warning", c[i]
            elif ex["use_trend_flip"] and len(lows) >= 2 and lows[-1][0] > entry_i \
                    and lows[-1][1] > lows[-2][1] and highs and c[i] > highs[-1][1]:
                reason, px = "trend_flip_up", c[i]
            mark = px if reason else c[i]
            pos.path.append((pos.entry_price - mark) / pos.entry_price - fund_per_bar * pos.bars_held)
            if reason:
                gross = (pos.entry_price - px) / pos.entry_price
                pos.exit_time, pos.exit_price, pos.exit_reason = t[i], px, reason
                pos.pnl_usd = qty * (pos.entry_price * gross - 2 * usd_side - pos.entry_price * fund_per_bar * pos.bars_held)
                pos.net_return = pos.pnl_usd / (qty * pos.entry_price)
                trades.append(pos)
                note(i, "exit", f"{reason} @ ${px:,.0f}  P&L ${pos.pnl_usd:+,.0f}")
                pos = None
            continue

        # 3) look for a fresh break while the trend is UP
        # Pattern: highs[-3] < highs[-2] (the top, a higher high made while the trend
        # was UP) > highs[-1] (lower high), then a close below the pullback low
        # between the top and the lower high (the "neckline").
        if setup is None and len(highs) >= 3 and lows:
            pre, top, lower_high = highs[-3], highs[-2], highs[-1]
            between = [p for k, p in lows if top[0] < k < lower_high[0]]
            level = min(between) if between else lows[-1][1]
            fresh = c[i - 1] >= level or lower_high[0] == j    # just crossed, or LH confirmed this bar
            if (top[0] > used_top and top[3] == 1 and top[1] > pre[1] and lower_high[1] < top[1]
                    and c[i] < level and fresh and i - lower_high[0] <= 3 * L + rt["max_bars"]):
                ok = True
                if lay["divergence"]:
                    ok &= top[2] < pre[2]
                if lay["volume"]:
                    ok &= vsma[i] > 0 and vol[i] >= cfg["volume"]["multiplier"] * vsma[i]
                used_top = top[0]                     # one attempt per top
                if ok:
                    setup = {"bar": i, "level": level, "top": top[1], "lh": lower_high[1], "top_i": top[0]}
                    note(i, "break", f"close {c[i]:.2f} < HL {level:.2f}; top {top[1]:.2f} LH {lower_high[1]:.2f}")

        # 4) enter on the break bar or on a failed retest
        if setup is not None:
            age = i - setup["bar"]
            if c[i] > setup["lh"] or age > rt["max_bars"]:
                note(i, "setup_cancel", "invalidated" if c[i] > setup["lh"] else "retest window expired")
                setup = None
                continue
            if lay["retest"]:
                trig = age > 0 and h[i] >= setup["level"] - rt["tolerance_atr"] * atr_[i] and c[i] < setup["level"]
            else:
                trig = c[i] < setup["level"]
            if trig and entry_ok(i) and signals_only:
                trades.append(Trade(entry_time=t[i], entry_price=c[i], break_level=setup["level"],
                                    top=setup["top"], top_time=t[setup["top_i"]], entry_i=i))
                note(i, "signal", f"reversal signal @ {c[i]:,.2f}")
                setup = None
            elif trig and entry_ok(i):
                entry = c[i]
                stop = min(setup["lh"] + ex["stop_buffer_atr"] * atr_[i], entry * (1 + ex["hard_stop_pct"]))
                low_since, entry_i = l[i], i
                pos = Trade(entry_time=t[i], entry_price=entry, break_level=setup["level"], top=setup["top"],
                            top_time=t[setup["top_i"]], entry_i=i)
                note(i, "entry", f"short @ ${entry:,.0f}  stop ${stop:,.0f}")
                setup = None

    if pos is not None:   # still open at the end: mark it, flagged as open
        gross = (pos.entry_price - c[-1]) / pos.entry_price
        pos.exit_time, pos.exit_price, pos.exit_reason = t[-1], c[-1], "open"
        pos.pnl_usd = qty * (pos.entry_price * gross - 2 * usd_side - pos.entry_price * fund_per_bar * pos.bars_held)
        pos.net_return = pos.pnl_usd / (qty * pos.entry_price)
        trades.append(pos)
    return trades
