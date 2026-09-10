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
from datetime import datetime

from .config import Config
from .data import get_candles
from .risk import ChallengeGuard, RiskDecision
from .trend import Trend, analyze

log = logging.getLogger(__name__)


@dataclass
class Signal:
    action: str            # "buy" | "sell" | "flatten" | "none"
    trend: Trend
    price: float
    aligned: dict[str, str]
    risk: RiskDecision | None = None   # populated when a ChallengeGuard is active


class StrategyRunner:
    def __init__(self, cfg: Config, notifier=None, guard: ChallengeGuard | None = None,
                 equity_fn=None, position_fn=None, now_fn=None):
        self.cfg = cfg
        self.notifier = notifier
        self._last_trend: Trend = Trend.NEUTRAL
        self._entry_dir: int = 0     # last direction we entered on

        # Prop-firm challenge gating (optional). ``guard`` enforces the rules;
        # ``equity_fn``/``position_fn`` report live account state in dollars /
        # contracts; ``now_fn`` supplies the current time (defaults to now).
        if guard is None and cfg.challenge_enabled:
            guard = ChallengeGuard(cfg.build_challenge_params())
        self.guard = guard
        self._equity_fn = equity_fn
        self._position_fn = position_fn
        self._now_fn = now_fn or datetime.now

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

        action, risk = self._apply_guard(action)
        return Signal(action=action, trend=htf_trend, price=price, aligned=aligned,
                      risk=risk)

    # -- prop-firm challenge gating ------------------------------------------
    def _apply_guard(self, action: str) -> tuple[str, RiskDecision | None]:
        """Let the ChallengeGuard veto entries and force flattens.

        A forced flatten always wins; a blocked entry is downgraded to "none".
        When no guard is configured the strategy signal passes through unchanged.
        """
        if self.guard is None:
            return action, None

        equity = self._equity_fn() if self._equity_fn else self.cfg.challenge_start_balance
        position = self._position_fn() if self._position_fn else 0.0
        decision = self.guard.update(self._now_fn(), equity, position)

        if decision.must_flatten:
            # Only emit a flatten when something is actually open.
            return ("flatten" if position != 0 else "none"), decision
        if action in ("buy", "sell") and not decision.can_enter:
            log.info("entry blocked by challenge guard: %s", decision.reason)
            self._entry_dir = 0     # re-arm; we did not actually take the trade
            return "none", decision
        return action, decision
