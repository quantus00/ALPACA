"""Grade reversal signals in BOTH directions by price accuracy, in dollars.

Top signals    (uptrend -> downtrend): the detector's bearish rules.
Bottom signals (downtrend -> uptrend): the same rules run on the mirrored
price series (``detector.invert``), so both directions use identical logic.

Per signal:
  extreme_price   the swing top/bottom the signal says has turned
  late_usd        dollars between that top/bottom and the signal price
  move_usd        how far price then went in the signal's direction before it
                  closed back beyond the top/bottom (or the next opposite signal)
  turn_price      where that move actually ended (the real bottom/top)
  against_usd     worst move against the signal before the turn
  valid           move_usd >= max(late_usd, confirm_atr x ATR)

Per big real turn (hindsight zigzag, >= big_turn_pct reversal):
  caught          a signal in that direction fired before the next big turn
  late_usd        dollars from the real top/bottom to the first such signal
All dollar figures are per 1 BTC.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .detector import invert, prepare, run


def signals_both(df: pd.DataFrame, htf_df: pd.DataFrame | None, tf: str, cfg: dict, layers: dict | None):
    """Return (prepared_df, top_signals, bottom_signals). Bottom signals keep mirrored prices."""
    d = prepare(df, tf, htf_df, cfg)
    di = prepare(invert(df), tf, invert(htf_df) if htf_df is not None else None, cfg)
    tops = run(d, cfg, layers, signals_only=True)
    bottoms = run(di, cfg, layers, signals_only=True)
    return d, di, tops, bottoms


def _grade(d: pd.DataFrame, sigs, opposite_idx: np.ndarray, cfg: dict, side: str, sign: float) -> list[dict]:
    """Grade signals on a frame where the signal is bearish. ``sign`` = 1 real, -1 mirrored."""
    lo, hi, cl, atr = (d[k].to_numpy(float) for k in ("low", "high", "close", "atr"))
    n = len(cl)
    k_atr = cfg["accuracy"]["confirm_atr"]
    rows = []
    for s in sigs:
        i, S, E = s.entry_i, s.entry_price, s.top
        nxt = opposite_idx[opposite_idx > i]
        stop_at = int(nxt[0]) if len(nxt) else n
        beyond = np.nonzero(cl[i + 1:stop_at] > E)[0]
        end = i + 1 + int(beyond[0]) if len(beyond) else stop_at      # exclusive
        if end <= i + 1:
            move, k, against = 0.0, i, 0.0
        else:
            k = i + 1 + int(lo[i + 1:end].argmin())
            move = S - lo[k]
            against = max(0.0, hi[i + 1:k + 1].max() - S) if k > i else 0.0
        late = E - S
        valid = move >= max(late, k_atr * atr[i]) and move > 0
        rows.append({
            "side": side,
            "signal_time": d.index[i] + (d.index[1] - d.index[0]),     # bar close
            "signal_price": round(sign * S, 2),
            "extreme_time": s.top_time, "extreme_price": round(sign * E, 2),
            "late_usd": round(late, 2),
            "turn_time": d.index[k], "turn_price": round(sign * lo[k], 2),
            "move_usd": round(move, 2), "against_usd": round(against, 2),
            "valid": bool(valid),
            "invalidated": bool(len(beyond)),
        })
    return rows


def grade_signals(d, di, tops, bottoms, cfg) -> pd.DataFrame:
    top_idx = np.array([s.entry_i for s in tops], int)
    bot_idx = np.array([s.entry_i for s in bottoms], int)
    rows = _grade(d, tops, bot_idx, cfg, "top", 1.0) + _grade(di, bottoms, top_idx, cfg, "bottom", -1.0)
    return pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True) if rows else pd.DataFrame()


def zigzag(h: np.ndarray, l: np.ndarray, pct: float) -> list[tuple[int, float, str]]:
    """Hindsight swing points that reversed by >= pct. Returns alternating (bar, price, 'top'|'bottom')."""
    pts: list[tuple[int, float, str]] = []
    direction, hi_i, lo_i, cand = 0, 0, 0, 0
    for i in range(len(h)):
        if direction == 0:
            hi_i = i if h[i] > h[hi_i] else hi_i
            lo_i = i if l[i] < l[lo_i] else lo_i
            if l[i] <= h[hi_i] * (1 - pct):
                pts.append((hi_i, h[hi_i], "top")); direction, cand = -1, i
            elif h[i] >= l[lo_i] * (1 + pct):
                pts.append((lo_i, l[lo_i], "bottom")); direction, cand = 1, i
        elif direction == -1:                       # falling: track the low
            cand = i if l[i] < l[cand] else cand
            if h[i] >= l[cand] * (1 + pct):
                pts.append((cand, l[cand], "bottom")); direction, cand = 1, i
        else:                                       # rising: track the high
            cand = i if h[i] > h[cand] else cand
            if l[i] <= h[cand] * (1 - pct):
                pts.append((cand, h[cand], "top")); direction, cand = -1, i
    return pts


def grade_turns(d: pd.DataFrame, graded: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """For each big real turn, the first signal in its direction before the next big turn.

    ``d`` is the price frame the turns are measured on — pass the finest data you
    have (1h) for every timeframe so all timeframes are graded against the same turns.
    """
    h, l = d["high"].to_numpy(float), d["low"].to_numpy(float)
    pts = zigzag(h, l, cfg["accuracy"]["big_turn_pct"])
    rows = []
    for n, (i, p, kind) in enumerate(pts):
        nxt_i = pts[n + 1][0] if n + 1 < len(pts) else len(h) - 1
        nxt_p = pts[n + 1][1] if n + 1 < len(pts) else (l[i:].min() if kind == "top" else h[i:].max())
        t0, t1 = d.index[i], d.index[nxt_i] + (d.index[1] - d.index[0])
        cand = graded[(graded.side == kind) & (graded.signal_time > t0) & (graded.signal_time <= t1)] \
            if len(graded) else graded
        first = cand.iloc[0] if len(cand) else None
        sig = float(first.signal_price) if first is not None else np.nan
        rows.append({
            "turn": kind, "turn_time": t0, "turn_price": round(p, 2),
            "move_end_time": d.index[nxt_i], "move_end_price": round(nxt_p, 2),
            "move_usd": round(abs(p - nxt_p), 2),
            "caught": first is not None,
            "signal_time": first.signal_time if first is not None else pd.NaT,
            "signal_price": sig,
            "late_usd": round(abs(p - sig), 2) if first is not None else np.nan,
            "left_usd": round(abs(sig - nxt_p), 2) if first is not None else np.nan,   # move left after the signal
        })
    return pd.DataFrame(rows)
