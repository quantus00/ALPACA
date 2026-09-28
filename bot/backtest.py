"""Backtest the Alex strategy on ICC instruments — crypto + futures, keyless.

Reuses the already-tested engine (``alex_bot.strategy`` for signals,
``alex_fx.backtest`` for the bar-by-bar walk with intrabar stop/TP and trend-flip
exits) and layers on dollar P/L using the instrument's contract multiplier.

Data (no API key needed):
  * crypto  -> Coinbase public candles. The BTC perp / nano perp track BTC-USD
               spot, so all three backtest on the same spot series.
  * futures -> Yahoo Finance continuous-contract tickers (MES=F, ES=F, MGC=F,
               GC=F, ...).
  * csv     -> your own OHLC file (time,open,high,low,close[,volume]).

Runs anywhere with outbound internet (your droplet / PC). Order execution is
separate (bot/brokers) — a backtest never touches a broker or a credential.
"""
from __future__ import annotations

import logging

import requests

from alex_bot.strategy import Candle, Params
from alex_fx.backtest import backtest as _walk
from alex_fx.data import parse_yahoo

from .config import AssetClass, Config

log = logging.getLogger("alex.backtest")

# Futures root -> Yahoo continuous-contract symbol (keyless).
_FUTURES_YF: dict[str, str] = {
    "MES": "MES=F", "ES": "ES=F",       # S&P 500 (micro / e-mini)
    "MNQ": "MNQ=F", "NQ": "NQ=F",       # Nasdaq-100
    "M2K": "M2K=F", "RTY": "RTY=F",     # Russell 2000
    "MYM": "MYM=F", "YM": "YM=F",       # Dow
    "MGC": "MGC=F", "GC": "GC=F",       # Gold (micro / full)
    "MCL": "MCL=F", "CL": "CL=F",       # Crude oil
}
_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "60m", "4h": "60m", "1d": "1d"}
_YF_RANGE = {"5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d",
             "4h": "730d", "1d": "5y"}


def yahoo_futures_symbol(symbol: str) -> str:
    """'MES'/'MESU5' -> 'MES=F'; unknown roots try '<SYM>=F'."""
    s = symbol.upper()
    for root in sorted(_FUTURES_YF, key=len, reverse=True):
        if s.startswith(root):
            return _FUTURES_YF[root]
    return f"{s.split('=')[0]}=F"


def _yahoo_candles(yf_symbol: str, tf: str, limit: int) -> list[Candle]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
    params = {"interval": _YF_INTERVAL.get(tf, "15m"),
              "range": _YF_RANGE.get(tf, "60d")}
    resp = requests.get(url, params=params, timeout=20,
                        headers={"User-Agent": "Mozilla/5.0 icc-alex/1.0"})
    resp.raise_for_status()
    return parse_yahoo(resp.json())[-limit:]


def fetch_candles(cfg: Config, tf: str, source: str = "auto",
                  csv: str | None = None, bars: int = 500) -> list[Candle]:
    """Historical candles for the config's symbol/asset, keyless."""
    if source == "csv" or csv:
        from alex_fx.data import load_csv
        return load_csv(csv)[-bars:]
    if cfg.asset_class == AssetClass.CRYPTO or source == "coinbase":
        from alex_bot.data import coinbase_candles          # perp -> BTC-USD spot
        return coinbase_candles(cfg.symbol(), tf, bars)
    if cfg.asset_class == AssetClass.FUTURES or source == "yahoo":
        return _yahoo_candles(yahoo_futures_symbol(cfg.symbol()), tf, bars)
    raise ValueError(f"no backtest data source for asset {cfg.asset_class}")


def _dollar_pnl(res, size: float, multiplier: float) -> float:
    """Total realized $ P/L across closed trades for the given size + point value."""
    total = 0.0
    for t in res.closed:
        move = (t.exit - t.entry) if t.side == "buy" else (t.entry - t.exit)
        total += move * size * multiplier
    return total


def run_backtest(cfg: Config, params: Params, tf: str, source: str = "auto",
                 csv: str | None = None, bars: int = 500, warmup: int = 60,
                 show_trades: bool = False) -> "object":
    """Fetch candles, walk the strategy, and print R-multiple + dollar results."""
    candles = fetch_candles(cfg, tf, source, csv, bars)
    if len(candles) < warmup + 5:
        raise SystemExit(f"not enough candles ({len(candles)}); need > {warmup + 5}. "
                         "Try a longer --bars, a bigger timeframe, or --csv.")
    res = _walk(candles, cfg.symbol(), params, warmup=warmup,
                spread_pips=0.0, exit_on_flip=True)
    mult = cfg.multiplier()
    dollars = _dollar_pnl(res, cfg.contract_size, mult)
    print(f"=== ALEX backtest {cfg.symbol()} ({cfg.asset_class.value}) "
          f"tf={tf} bars={len(candles)} mult={mult:g} size={cfg.contract_size:g} ===")
    print(res.summary(pips=False))
    print(f"  est. $ P/L    : {dollars:+.2f}  (size {cfg.contract_size:g} x "
          f"mult {mult:g})")
    if show_trades:
        for t in res.closed:
            print(f"  {t.side:<4} in {t.entry:g} out {t.exit:g} "
                  f"[{t.reason}] {t.r_multiple:+.2f}R")
    return res
