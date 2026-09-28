"""Alex market-structure strategy, wired into the ICC app's broker layer.

Runs from the ICC app (``python -m bot.main alex ...``) and trades **any** asset
the chosen broker supports in free-symbol mode:
  * Coinbase — every spot pair (BTC-USD, ETH-USD, SOL-USD, ...)
  * Webull   — crypto and futures via the official Webull OpenAPI

The strategy itself is the already-tested engine in ``alex_bot.strategy`` (trend
-> area-of-interest -> rejection/engulfing entry). This module only handles data
feeds, position sizing, order routing through the ICC brokers, and stop /
take-profit / trend-flip exits. Everything is dry-run unless ``--live`` arms it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable

from alex_bot.strategy import Candle, Params, Signal, Trend, evaluate

from .brokers import get_broker
from .brokers.base import BrokerBase
from .config import AssetClass, Config
from .notifier import Notifier

log = logging.getLogger("alex")

# fetch(symbol, timeframe, limit) -> list[Candle]
Fetch = Callable[[str, str, int], list]


# ---------------------------------------------------------------------------
# Data feeds (per asset class)
# ---------------------------------------------------------------------------

def _crypto_candles(symbol: str, tf: str, limit: int = 300) -> list[Candle]:
    """Keyless Coinbase public candles for any spot pair (via alex_bot.data)."""
    from alex_bot.data import coinbase_candles
    return coinbase_candles(symbol, tf, limit)


def _futures_candles(cfg: Config, symbol: str, tf: str, limit: int = 300) -> list[Candle]:
    """Futures candles via the Webull OpenAPI market-data SDK (guarded).

    Isolated seam: confirm the market-data call against your installed
    ``webull-python-sdk-*`` version. Raises a clear error when unavailable so
    the strategy never trades on missing data.
    """
    from .brokers.webull_openapi import WebullOpenAPIBroker
    broker = get_broker(cfg)
    if not isinstance(broker, WebullOpenAPIBroker):
        raise RuntimeError("futures candles require the Webull OpenAPI broker")
    try:
        api = broker._client()  # noqa: SLF001 - intentional single seam
        gran = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
                "1h": "H1", "4h": "H4", "1d": "D1"}.get(tf, "M15")
        rows = api.market_data.get_bars(symbol.upper(), "FUTURES", gran, limit)  # seam
        out: list[Candle] = []
        for r in (rows or []):
            d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
            out.append(Candle(time=int(d.get("timestamp") or d.get("time") or 0),
                              open=float(d["open"]), high=float(d["high"]),
                              low=float(d["low"]), close=float(d["close"]),
                              volume=float(d.get("volume", 0) or 0)))
        if not out:
            raise RuntimeError("Webull returned no futures bars")
        return out[-limit:]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"could not fetch Webull futures candles for {symbol} ({exc}); "
            "confirm the market-data SDK seam in bot/alex.py._futures_candles") from exc


def make_fetch(cfg: Config) -> Fetch:
    """Return a fetch(symbol, tf, limit) for the config's asset class."""
    if cfg.asset_class == AssetClass.CRYPTO:
        return lambda symbol, tf, limit=300: _crypto_candles(symbol, tf, limit)
    if cfg.asset_class == AssetClass.FUTURES:
        return lambda symbol, tf, limit=300: _futures_candles(cfg, symbol, tf, limit)
    raise ValueError(f"alex mode supports crypto/futures, not {cfg.asset_class}")


# ---------------------------------------------------------------------------
# Sizing + evaluation
# ---------------------------------------------------------------------------

def position_size(equity: float, risk_pct: float, entry: float, stop: float,
                  fallback: float) -> float:
    """Risk-based size; falls back to the fixed contract size when risk sizing
    is not configured (equity/risk <= 0) or the stop distance is degenerate."""
    per_unit = abs(entry - stop)
    if per_unit <= 0 or equity <= 0 or risk_pct <= 0:
        return fallback
    return (equity * (risk_pct / 100.0)) / per_unit


def _alex_settings(cfg: Config) -> tuple[float, float, str, str, int]:
    """(equity, risk_pct, entry_tf, structure_tf, poll_seconds) from env."""
    equity = float(os.getenv("ALEX_EQUITY", "0") or 0)
    risk = float(os.getenv("ALEX_RISK", "0") or 0)
    entry_tf = os.getenv("ALEX_ENTRY_TF", "15m")
    structure_tf = os.getenv("ALEX_STRUCTURE_TF", "1h")
    poll = int(cfg.poll_seconds)
    return equity, risk, entry_tf, structure_tf, poll


def evaluate_alex(cfg: Config, params: Params, fetch: Fetch,
                  entry_tf: str, structure_tf: str) -> Signal:
    structure = fetch(cfg.symbol(), structure_tf, 300)
    entry = fetch(cfg.symbol(), entry_tf, 300)
    return evaluate(structure, entry, params)


def analyze(cfg: Config, params: Params, fetch: Fetch | None = None,
            sig: Signal | None = None) -> Signal:
    _eq, _risk, entry_tf, structure_tf, _poll = _alex_settings(cfg)
    fetch = fetch or make_fetch(cfg)
    sig = sig or evaluate_alex(cfg, params, fetch, entry_tf, structure_tf)
    print(f"=== ALEX {cfg.describe()} ===")
    print(f"Trend:  {sig.trend.label()}")
    print(f"Signal: {sig.action.upper()}  — {sig.reason}")
    if sig.action in ("buy", "sell"):
        eq, risk, *_ = _alex_settings(cfg)
        qty = position_size(eq, risk, sig.entry, sig.stop, cfg.contract_size)
        rr = abs(sig.take_profit - sig.entry) / max(abs(sig.entry - sig.stop), 1e-9)
        print(f"  pattern : {sig.pattern}")
        print(f"  entry {sig.entry:g}  stop {sig.stop:g}  tp {sig.take_profit:g}  (R:R {rr:.1f})")
        print(f"  size    : {qty:g}")
    return sig


# ---------------------------------------------------------------------------
# Position state (single position per run, separate from the dual layer)
# ---------------------------------------------------------------------------

def _state_path(cfg: Config) -> str:
    return os.getenv("BOT_ALEX_STATE_FILE", "alex_icc_state.json")


def _load(cfg: Config) -> dict | None:
    try:
        with open(_state_path(cfg)) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save(cfg: Config, pos: dict) -> None:
    path = _state_path(cfg)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(pos, f, indent=2)
    os.replace(tmp, path)


def _clear(cfg: Config) -> None:
    try:
        os.remove(_state_path(cfg))
    except OSError:
        pass


def trade_tick(cfg: Config, params: Params, broker: BrokerBase, fetch: Fetch,
               notifier: Notifier | None = None) -> None:
    """One decision cycle: manage an open position, else look for an entry."""
    _eq, _risk, entry_tf, structure_tf, _poll = _alex_settings(cfg)
    sig = evaluate_alex(cfg, params, fetch, entry_tf, structure_tf)
    pos = _load(cfg)

    if pos:  # manage exits: stop / take-profit / trend flip against us
        price = broker.mark_price(pos.get("symbol"), pos.get("meta")) or sig.price
        side = pos["side"]
        hit_stop = price <= pos["stop"] if side == "buy" else price >= pos["stop"]
        hit_tp = price >= pos["tp"] if side == "buy" else price <= pos["tp"]
        flipped = (side == "buy" and sig.trend == Trend.DOWN) or \
                  (side == "sell" and sig.trend == Trend.UP)
        if hit_stop or hit_tp or flipped:
            reason = "stop" if hit_stop else "take-profit" if hit_tp else "trend-flip"
            f = broker.flatten(side, pos["qty"], pos.get("symbol"), pos.get("meta"))
            log.info("EXIT (%s) %s -> ok=%s %s", reason, cfg.symbol(), f.ok,
                     f.error or f.order_id)
            if f.ok:
                _clear(cfg)
        else:
            log.info("holding %s %s: price %g stop %g tp %g", side, cfg.symbol(),
                     price, pos["stop"], pos["tp"])
        return

    if sig.action in ("buy", "sell"):
        eq, risk, *_ = _alex_settings(cfg)
        qty = position_size(eq, risk, sig.entry, sig.stop, cfg.contract_size)
        f = broker.place_order(sig.action, qty)
        log.info("ENTER %s %s %g -> ok=%s %s", sig.action.upper(), cfg.symbol(),
                 qty, f.ok, f.error or f.order_id)
        if f.ok:
            if notifier:
                notifier.notify_entry(sig.action, cfg.symbol(), sig.entry, qty)
            _save(cfg, {"side": sig.action, "qty": qty, "entry": sig.entry,
                        "stop": sig.stop, "tp": sig.take_profit,
                        "symbol": f.symbol or cfg.symbol(),
                        "order_id": f.order_id, "meta": f.meta})
    else:
        log.info("flat — %s", sig.reason)


def run_loop(cfg: Config, params: Params) -> None:
    _eq, _risk, entry_tf, structure_tf, poll = _alex_settings(cfg)
    fetch = make_fetch(cfg)
    broker = get_broker(cfg)
    notifier = Notifier()
    armed = "LIVE-ARMED" if not cfg.dry_run else "paper/dry-run"
    log.info("ALEX loop: %s  entry=%s structure=%s every %ss [%s]",
             cfg.describe(), entry_tf, structure_tf, poll, armed)
    while True:
        try:
            trade_tick(cfg, params, broker, fetch, notifier)
        except Exception:  # noqa: BLE001
            log.exception("alex tick failed")
        time.sleep(poll)


def params_from(args) -> Params:
    return Params(
        pivot_lookback=getattr(args, "pivot_lookback", 3),
        aoi_tol_frac=getattr(args, "aoi_tol", 0.0015),
        min_touches=getattr(args, "min_touches", 3),
        wick_ratio=getattr(args, "wick_ratio", 1.5),
        rr=getattr(args, "rr", 2.0),
    )
