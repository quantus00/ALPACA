"""Incremental 1h features the hedge rules need.

Everything here is computed bar-by-bar from CLOSED 1h bars only, so the
backtest and paper mode see exactly the same values and nothing looks ahead:

  * ATR(14) — Wilder/RMA, identical to Pine ``ta.atr``.
  * 1h structure trend + last confirmed 1h swing high/low (ICC correction /
    continuation levels). A pivot at bar i is only known at bar i+lookback.
  * 4h structure trend, built from 1h bars aggregated into UTC 4h buckets; a
    4h bucket only counts once its last hour has closed.
  * Rolling correlation of 1h returns, primary vs hedge (forex gate).

The swing/trend rules mirror ``bot/trend.py`` (HH+HL = UP, LH+LL = DOWN, both
legs must break to flip).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ..trend import Candle

H1 = 3600
H4 = 4 * 3600


class StructureTracker:
    """Streaming version of ``bot.trend.analyze`` with pivot confirmation delay."""

    def __init__(self, lookback: int) -> None:
        self.lb = lookback
        self.win: deque[Candle] = deque(maxlen=2 * lookback + 1)
        self.last_ph: float | None = None
        self.prev_ph: float | None = None
        self.last_pl: float | None = None
        self.prev_pl: float | None = None
        self.state = 0

    def update(self, c: Candle) -> int:
        self.win.append(c)
        if len(self.win) < self.win.maxlen:
            return self.state
        lb = self.lb
        bars = list(self.win)
        mid = bars[lb]
        left, right = bars[:lb], bars[lb + 1:]
        is_ph = all(mid.high > b.high for b in left) and all(mid.high >= b.high for b in right)
        is_pl = all(mid.low < b.low for b in left) and all(mid.low <= b.low for b in right)
        # Same event order as trend.analyze: highs and lows on one bar are both applied.
        for kind in ("H", "L"):
            if kind == "H" and is_ph:
                self.prev_ph, self.last_ph = self.last_ph, mid.high
            elif kind == "L" and is_pl:
                self.prev_pl, self.last_pl = self.last_pl, mid.low
            else:
                continue
            self._classify()
        return self.state

    def _classify(self) -> None:
        if None in (self.last_ph, self.prev_ph, self.last_pl, self.prev_pl):
            return
        hh, lh = self.last_ph > self.prev_ph, self.last_ph < self.prev_ph
        hl, ll = self.last_pl > self.prev_pl, self.last_pl < self.prev_pl
        if hh and hl:
            self.state = 1
        elif lh and ll:
            self.state = -1


class ATR:
    def __init__(self, length: int) -> None:
        self.n = length
        self.prev_close: float | None = None
        self.trs: list[float] = []
        self.value: float | None = None

    def update(self, c: Candle) -> float | None:
        if self.prev_close is None:
            tr = c.high - c.low
        else:
            tr = max(c.high - c.low, abs(c.high - self.prev_close), abs(c.low - self.prev_close))
        self.prev_close = c.close
        if self.value is None:
            self.trs.append(tr)
            if len(self.trs) == self.n:
                self.value = sum(self.trs) / self.n
        else:
            self.value = (self.value * (self.n - 1) + tr) / self.n
        return self.value


class FourHourBuilder:
    """Aggregate 1h bars into 4h buckets; emit a bucket once its final hour closes
    (or when a bar from a later bucket arrives, e.g. across an FX weekend gap)."""

    def __init__(self) -> None:
        self.cur: Candle | None = None

    def update(self, c: Candle) -> list[Candle]:
        done: list[Candle] = []
        start = c.time - (c.time % H4)
        if self.cur is not None and start != self.cur.time:
            done.append(self.cur)
            self.cur = None
        if self.cur is None:
            self.cur = Candle(time=start, open=c.open, high=c.high, low=c.low,
                              close=c.close, volume=c.volume)
        else:
            self.cur.high = max(self.cur.high, c.high)
            self.cur.low = min(self.cur.low, c.low)
            self.cur.close = c.close
            self.cur.volume += c.volume
        if (c.time + H1) % H4 == 0:
            done.append(self.cur)
            self.cur = None
        return done


class RollingCorr:
    def __init__(self, window_days: int) -> None:
        self.window = window_days * 86400
        self.buf: deque[tuple[int, float, float]] = deque()

    def update(self, t: int, x: float, y: float) -> None:
        self.buf.append((t, x, y))
        while self.buf and self.buf[0][0] <= t - self.window:
            self.buf.popleft()

    def value(self, min_obs: int) -> float | None:
        n = len(self.buf)
        if n < max(min_obs, 3):
            return None
        xs = [b[1] for b in self.buf]
        ys = [b[2] for b in self.buf]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((a - mx) ** 2 for a in xs)
        syy = sum((b - my) ** 2 for b in ys)
        sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        if sxx <= 0 or syy <= 0:
            return None
        return sxy / math.sqrt(sxx * syy)


@dataclass
class BarContext:
    """Everything the rule engine sees on one closed 1h bar."""
    time: int                       # bar OPEN time (unix s); bar closes at time + 3600
    open: float
    high: float
    low: float
    close: float
    atr: float | None
    trend_1h: int
    trend_4h: int
    swing_high_1h: float | None     # last confirmed 1h swing high
    swing_low_1h: float | None      # last confirmed 1h swing low
    h_open: float                   # hedge-leg OHLC (crypto: perp, or spot as proxy)
    h_high: float
    h_low: float
    h_close: float
    corr: float | None = None       # rolling corr of 1h returns (forex only)
    funding_rate_pct: float | None = None   # funding rate settled at this bar close, % (crypto)

    @property
    def close_time(self) -> int:
        return self.time + H1


class FeatureBuilder:
    def __init__(self, atr_length: int, pivot_lookback: int, corr_window_days: int = 30,
                 corr_min_obs: int = 200) -> None:
        self.atr = ATR(atr_length)
        self.s1h = StructureTracker(pivot_lookback)
        self.s4h = StructureTracker(pivot_lookback)
        self.b4h = FourHourBuilder()
        self.corr = RollingCorr(corr_window_days)
        self.corr_min_obs = corr_min_obs
        self._prev_p: float | None = None
        self._prev_h: float | None = None
        self._h4_from = 0

    def seed_4h(self, closed_4h: list[Candle]) -> None:
        """Warm the 4h structure from real 4h candles (paper mode), so it matches
        the bot's own 4H read; later 1h bars only build buckets after these."""
        for b in closed_4h:
            self.s4h.update(b)
        if closed_4h:
            self._h4_from = closed_4h[-1].time + H4

    def update(self, c: Candle, hedge: Candle | None = None,
               funding_rate_pct: float | None = None, with_corr: bool = False) -> BarContext:
        atr = self.atr.update(c)
        t1 = self.s1h.update(c)
        if c.time >= self._h4_from:
            for b in self.b4h.update(c):
                self.s4h.update(b)
        h = hedge or c
        corr = None
        if with_corr:
            if self._prev_p and self._prev_h:
                self.corr.update(c.time, c.close / self._prev_p - 1.0, h.close / self._prev_h - 1.0)
            corr = self.corr.value(self.corr_min_obs)
        self._prev_p, self._prev_h = c.close, h.close
        return BarContext(
            time=c.time, open=c.open, high=c.high, low=c.low, close=c.close,
            atr=atr, trend_1h=t1, trend_4h=self.s4h.state,
            swing_high_1h=self.s1h.last_ph, swing_low_1h=self.s1h.last_pl,
            h_open=h.open, h_high=h.high, h_low=h.low, h_close=h.close,
            corr=corr, funding_rate_pct=funding_rate_pct,
        )
