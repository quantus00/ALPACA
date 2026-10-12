"""1h structure break -> 15m pullback entry, with fixed R:R targets.

For a TOP (short):
  1. The 1h detector fires: top, lower high, close below the neckline.
  2. Drop to 15m. The "leg" runs from the 1h top down to the lowest 15m low so far.
  3. PULLBACK: a 15m high retraces at least `min_retrace` of that leg.
     Cancelled if price goes above the 1h lower high or past `max_retrace`.
     If price makes a new leg low before triggering, the leg extends and we wait
     for a fresh pullback.
  4. ENTRY (continuation): a 15m close below the most recent confirmed 15m swing
     low that formed inside the pullback.
  5. STOP just above the pullback high (+ buffer x 15m ATR). TARGET = entry - RR x risk.
     If one 15m bar touches both, the stop is assumed first (conservative).
BOTTOMS (long) are the same rules on mirrored prices.

`mode="immediate"` instead enters at the 1h signal close with the stop above the
1h lower high — the comparison baseline.
One trade at a time per direction; a signal that arrives while a trade in that
direction is open is skipped. Nothing looks ahead: 15m bars are used only after
the 1h signal bar has closed, and 15m swing lows only once confirmed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .detector import _pivots


def simulate(sigs, d15: pd.DataFrame, cfg: dict, rr: float, mode: str = "pullback",
             sign: float = 1.0, side: str = "short", one_at_a_time: bool = True) -> list[dict]:
    """Run trades for bearish-frame signals on a bearish-frame 15m frame (mirrored for longs).

    ``one_at_a_time=False`` returns every entry (used to grade entry accuracy).
    """
    lt = cfg["ltf"]
    o, h, l, c = (d15[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = d15["atr"].to_numpy(float)
    t = d15.index
    n = len(c)
    k = lt["pivot_lookback"]
    _, is_pl = _pivots(h, l, k)
    max_wait = int(lt["max_wait_hours"] * 4)
    out: list[dict] = []
    busy_until = -1

    for s in sigs:
        start = int(t.searchsorted(s.entry_time + pd.Timedelta(hours=1)))   # first 15m bar after the 1h close
        if start >= n or (one_at_a_time and start <= busy_until):
            continue
        E, LH = s.top, s.lower_high
        entry_i, stop = None, None

        if mode == "immediate":
            entry_i, entry = start - 1, s.entry_price
            stop = LH + lt["stop_buffer_atr"] * atr[start - 1]
        else:
            top_i = int(t.searchsorted(s.top_time))
            leg_low = l[top_i:start].min() if start > top_i else l[start]
            active, ph, pull_start, trig = False, -np.inf, 0, None
            for i in range(start, min(n, start + max_wait)):
                if not active:
                    leg_low = min(leg_low, l[i])
                    if h[i] >= leg_low + lt["min_retrace"] * (E - leg_low):
                        active, ph, pull_start, trig = True, h[i], i, None
                    continue
                ph = max(ph, h[i])
                if h[i] > LH or ph > leg_low + lt["max_retrace"] * (E - leg_low):
                    break                                   # pullback went too far: setup dead
                if l[i] < leg_low:                          # new low before a trigger: leg extends
                    active, leg_low = False, l[i]
                    continue
                j = i - k                                   # 15m swing low confirmed at this bar
                if j > pull_start and is_pl[j]:
                    trig = l[j]
                if trig is not None and c[i] < trig:
                    entry_i, entry = i, c[i]
                    stop = ph + lt["stop_buffer_atr"] * atr[i]
                    break
        if entry_i is None:
            continue
        risk = stop - entry
        if risk <= 0:
            continue
        target = entry - rr * risk
        exit_i, exit_px, reason = n - 1, c[-1], "open"
        for i in range(entry_i + 1, n):
            if h[i] >= stop:
                exit_i, exit_px, reason = i, max(o[i], stop), "stop"
                break
            if l[i] <= target:
                exit_i, exit_px, reason = i, min(o[i], target), "target"
                break
        busy_until = exit_i
        bar = pd.Timedelta(minutes=15)
        out.append({
            "side": side,
            "signal_time": s.entry_time + pd.Timedelta(hours=1),
            "entry_time": t[entry_i] + bar, "entry": sign * entry,
            "stop": sign * stop, "target": sign * target, "risk_points": risk,
            "exit_time": t[exit_i] + bar if reason != "open" else t[exit_i], "exit": sign * exit_px,
            "reason": reason, "points": entry - exit_px,
            "hours": (exit_i - entry_i) / 4,
            "swing_extreme": sign * E,
            "entry_i": entry_i, "extreme_raw": E, "entry_raw": entry, "signal_i": s.entry_i,
        })
    return out
