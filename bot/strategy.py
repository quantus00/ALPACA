"""Strategy runner: 4H trend + multi-timeframe alignment -> signal.

This is the live counterpart of the Pine Script. It:
  1. Computes the 4H (configurable) master trend and notifies UPTREND/DOWNTREND
     on a flip.
  2. On a flip, waits for the 5m/15m/30m/1h timeframes to all align with the
     new 4H direction, then emits a BUY / SELL signal exactly once per flip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .data import get_candles
from .trend import Trend, analyze

log = logging.getLogger(__name__)


@dataclass
class Signal:
    action: str            # "buy" | "sell" | "none"
    trend: Trend
    price: float
    aligned: dict[str, str]


class StrategyRunner:
    def __init__(self, cfg: Config, notifier=None):
        self.cfg = cfg
        self.notifier = notifier
        self._last_trend: Trend = Trend.NEUTRAL
        self._entry_dir: int = 0     # last direction we entered on

    # -- helpers --------------------------------------------------------------
    def _trend_of(self, timeframe: str) -> tuple[Trend, float]:
        candles = get_candles(self.cfg.symbol(), timeframe, limit=300)
        if not candles:
            return Trend.NEUTRAL, 0.0
        res = analyze(candles, self.cfg.pivot_lookback)
        return res.trend, candles[-1].close

    # -- main tick ------------------------------------------------------------
    def evaluate(self) -> Signal:
        htf_trend, price = self._trend_of(self.cfg.trend_timeframe)

        # Notify on a confirmed 4H trend change.
        if htf_trend != Trend.NEUTRAL and htf_trend != self._last_trend:
            self._last_trend = htf_trend
            self._entry_dir = 0     # allow a fresh entry on the new trend
            msg = htf_trend.label()
            log.info("4H trend flip -> %s", msg)
            if self.notifier:
                self.notifier.notify_trend(msg, self.cfg.symbol(), price)

        # Gather alignment timeframes.
        aligned: dict[str, str] = {}
        states: list[Trend] = []
        for tf in self.cfg.alignment_timeframes:
            t, _ = self._trend_of(tf)
            aligned[tf] = t.label()
            states.append(t)

        all_up = htf_trend == Trend.UP and all(s == Trend.UP for s in states)
        all_down = htf_trend == Trend.DOWN and all(s == Trend.DOWN for s in states)

        action = "none"
        if all_up and self._entry_dir != 1:
            action, self._entry_dir = "buy", 1
        elif all_down and self._entry_dir != -1:
            action, self._entry_dir = "sell", -1
        elif not all_up and not all_down:
            # alignment lost -> re-arm so the next alignment can fire.
            self._entry_dir = 0

        return Signal(action=action, trend=htf_trend, price=price, aligned=aligned)
