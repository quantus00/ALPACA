"""Backtest: 1h structure-break reversal -> 15m pullback entry, fixed 2:1 / 3:1 targets.

Shorts after 1h TOP breaks, longs after 1h BOTTOM breaks (same rules, mirrored).
Compared against entering immediately at the 1h signal with the stop beyond the
1h lower high / higher low.

    python3 backtest/pullback_backtest.py --symbol BTC                  # BTCUSD_{15m,1h,1d}.csv
    python3 backtest/pullback_backtest.py --symbol GOLD --prefix XAUUSD # after fetch_histdata.py
    python3 backtest/pullback_backtest.py --symbol MES  --prefix SPX

Writes backtest/pullback_<SYMBOL>.md and trade logs in backtest/pullback_logs/.
Dollar P&L uses [instruments.<SYMBOL>] in reversal/config.toml.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reversal.detector import LAYERS, invert, load_config, position_size, prepare, run  # noqa: E402
from reversal.ltf_entry import simulate  # noqa: E402

HERE = Path(__file__).parent
PREFIX = {"BTC": "BTCUSD", "GOLD": "XAUUSD", "MES": "SPX"}
ET = "America/New_York"


def dollars(cfg: dict, symbol: str) -> tuple[float, float]:
    """($ per 1.0 price move for the whole position, $ cost per round trip)."""
    ins = cfg["instruments"][symbol]
    if ins.get("use_sizing"):
        qty, fee_btc_side, _ = position_size(cfg)
        return qty, 2 * qty * (fee_btc_side + cfg["costs"]["slippage_usd_per_btc_side"])
    upp = ins["usd_per_point"] * ins["contracts"]
    return upp, 2 * (ins["fee_usd_per_contract_side"] * ins["contracts"] + ins["slippage_points"] * upp)


def load(data_dir: Path, prefix: str, tf: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{prefix}_{tf}.csv", index_col="time", parse_dates=["time"]).sort_index()


def money(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def score(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return dict(n=0, win=np.nan, net=0.0, mdd=0.0, avg_win=np.nan, avg_loss=np.nan, risk=np.nan)
    eq = tr.pnl_usd.cumsum()
    mdd = float((eq.cummax().clip(lower=0) - eq).max())
    w, lo = tr[tr.pnl_usd > 0], tr[tr.pnl_usd <= 0]
    return dict(n=len(tr), win=len(w) / len(tr), net=tr.pnl_usd.sum(), mdd=mdd,
                avg_win=w.pnl_usd.mean() if len(w) else np.nan,
                avg_loss=lo.pnl_usd.mean() if len(lo) else np.nan, risk=tr.risk_usd.mean())


def cell(s: dict) -> str:
    if s["n"] == 0:
        return "0 | – | – | –"
    return f"{s['n']} | {s['win']:.0%} | {money(s['net'])} | {money(s['mdd'])}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC", choices=["BTC", "GOLD", "MES"])
    ap.add_argument("--prefix", default=None, help="file prefix, e.g. BTCUSD -> BTCUSD_15m.csv")
    ap.add_argument("--data-dir", default=str(HERE / "data" / "reversal"))
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)
    sym, prefix, data_dir = a.symbol, a.prefix or PREFIX[a.symbol], Path(a.data_dir)
    upp, cost_rt = dollars(cfg, sym)
    log_dir = HERE / "pullback_logs"
    log_dir.mkdir(exist_ok=True)

    d1h, d1d, d15 = load(data_dir, prefix, "1h"), load(data_dir, prefix, "1d"), load(data_dir, prefix, "15m")
    p15, p15i = prepare(d15, "15m", None, cfg), prepare(invert(d15), "15m", None, cfg)
    end = d15.index[-1]
    windows = {"full": d1h.index[0] + pd.Timedelta(days=60),
               "3y": end - pd.DateOffset(years=3), "12m": end - pd.DateOffset(years=1)}

    lines = [f"# {sym}: 1h structure break -> 15m pullback entry, fixed R:R", "",
             f"Position: ${upp:,.2f} per 1.0 price move; round-trip fees+slippage ${cost_rt:,.2f}. "
             f"Data {d15.index[0]:%b %Y} – {end:%b %d %Y}. Columns: trades | win rate | net P&L | max drawdown.", ""]
    for vname, lay in (("structure only", {k: False for k in LAYERS}),
                       ("structure + daily filter", {**{k: False for k in LAYERS}, "htf": True})):
        h1 = prepare(d1h, "1h", d1d, cfg)
        h1i = prepare(invert(d1h), "1h", invert(d1d), cfg)
        tops = run(h1, cfg, lay, signals_only=True)
        bots = run(h1i, cfg, lay, signals_only=True)
        lines += [f"## 1h setup: {vname}  ({len(tops)} top signals, {len(bots)} bottom signals)", "",
                  "| entry | R:R | side | " + " | ".join(f"{w}: trades | win | net | maxDD" for w in windows) + " |",
                  "|---|---|---|" + "---|" * (4 * len(windows))]
        for mode in ("pullback", "immediate"):
            for rr in cfg["ltf"]["rr"]:
                sh = simulate(tops, p15, cfg, rr, mode, 1.0, "short")
                lg = simulate(bots, p15i, cfg, rr, mode, -1.0, "long")
                tr = pd.DataFrame(sh + lg)
                if tr.empty:
                    continue
                tr["risk_usd"] = tr.risk_points * upp
                tr["pnl_usd"] = tr.points * upp - cost_rt
                tr = tr.sort_values("entry_time").reset_index(drop=True)
                label = "15m pullback" if mode == "pullback" else "at 1h signal"
                for side in ("short", "long", "both"):
                    sub = tr if side == "both" else tr[tr.side == side]
                    cells = [cell(score(sub[sub.entry_time >= s])) for s in windows.values()]
                    lines.append(f"| {label} | {rr:g}:1 | {side} | " + " | ".join(cells) + " |")
                if mode == "pullback":
                    logt = tr.copy()
                    for col in ("signal_time", "entry_time", "exit_time"):
                        logt[col] = logt[col].dt.tz_convert(ET).dt.strftime("%Y-%m-%d %I:%M %p")
                    for col in ("entry", "stop", "target", "exit", "swing_extreme", "risk_usd", "pnl_usd"):
                        logt[col] = logt[col].round(2)
                    tag = "daily_filter" if "daily" in vname else "structure"
                    logt.drop(columns=["points", "risk_points", "entry_i", "extreme_raw", "entry_raw", "signal_i"]).to_csv(
                        log_dir / f"{sym}_{tag}_{rr:g}to1.csv", index=False)
        lines.append("")
        print(vname, "done")
    lines.append("Times in the trade logs are New York time, at the close of the 15m bar. "
                 "Same-bar stop and target touches count as a stop.")
    out = HERE / f"pullback_{sym}.md"
    out.write_text("\n".join(lines))
    print("wrote", out)


if __name__ == "__main__":
    main()
