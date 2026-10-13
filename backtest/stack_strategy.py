"""BTC stack + perp reversal shorts: hold spot forever, short tops, buy BTC with profit.

  * Day one: buy $START of spot BTC. It is never sold.
  * Each TOP reversal signal (best setup from reversal_search: 1h swing 3, 4h EMA50,
    divergence, volume, MACD) opens a short of 1 perp contract at the 1h signal close.
    Stop = just above the 1h lower high. Target = 3:1.
  * ADD: if the short moves against you to 50% of the way to the stop, short 1 more
    contract there. The target is then re-set to 3:1 from the AVERAGE entry
    (same stop). Max one add per trade.
  * Perp profit collects in a bucket. Each time it reaches $THRESHOLD, buy that much
    spot BTC (spot fee applied). Losses come out of the bucket; if it goes negative
    that is cash you would have had to add — reported as "cash needed".
  * Trades are walked on 15m bars. If one 15m bar touches the add level and the stop,
    the add happens first, then the stop (worst case). Stop is checked before target.

    python3 backtest/stack_strategy.py [--start-usd 1000] [--thresholds 1000,100]
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from best_setup_winrate import BEST  # noqa: E402
from pullback_backtest import dollars, load, money  # noqa: E402
from reversal.detector import LAYERS, load_config, prepare, run  # noqa: E402

HERE = Path(__file__).parent
ET = "America/New_York"


def short_trades(sigs, d15: pd.DataFrame, cfg: dict, rr: float, add_at: float | None,
                 usd_per_point: float, cost_per_contract: float) -> pd.DataFrame:
    o, h, l, c = (d15[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = d15["atr"].to_numpy(float)
    t, n = d15.index, len(c)
    buf = cfg["ltf"]["stop_buffer_atr"]
    bar = pd.Timedelta(minutes=15)
    rows, busy = [], -1
    for s in sigs:
        start = int(t.searchsorted(s.entry_time + pd.Timedelta(hours=1)))
        if start >= n or start <= busy:
            continue
        entry = s.entry_price
        stop = s.lower_high + buf * atr[start - 1]
        risk = stop - entry
        if risk <= 0:
            continue
        entries = [entry]
        target = entry - rr * risk
        add_level = entry + add_at * risk if add_at is not None else np.inf
        add_time = None
        exit_i, exit_px, reason = n - 1, c[-1], "open"
        for i in range(start, n):
            if len(entries) == 1 and h[i] >= add_level:
                px = max(o[i], add_level)
                if px < stop:
                    entries.append(px)
                    avg = float(np.mean(entries))
                    target = avg - rr * (stop - avg)
                    add_time = t[i] + bar
            if h[i] >= stop:
                exit_i, exit_px, reason = i, max(o[i], stop), "stop"
                break
            if l[i] <= target:
                exit_i, exit_px, reason = i, min(o[i], target), "target"
                break
        busy = exit_i
        k = len(entries)
        pnl = sum(e - exit_px for e in entries) * usd_per_point - k * cost_per_contract
        rows.append({"signal_time": s.entry_time + pd.Timedelta(hours=1), "entry": entry, "stop": stop,
                     "first_target": entry - rr * risk, "added": k > 1, "add_price": entries[1] if k > 1 else np.nan,
                     "add_time": add_time, "final_target": target, "contracts": k,
                     "exit_time": t[exit_i] + bar if reason != "open" else t[exit_i],
                     "exit": exit_px, "reason": reason, "pnl_usd": pnl})
    return pd.DataFrame(rows)


def ledger(trades: pd.DataFrame, d1h: pd.DataFrame, start, start_usd: float, threshold: float,
           spot_fee: float) -> dict:
    px0 = float(d1h.loc[d1h.index >= start, "close"].iloc[0])
    btc0 = start_usd * (1 - spot_fee) / px0
    btc, bucket, min_bucket, buys = btc0, 0.0, 0.0, []
    tr = trades[(trades.signal_time >= start) & (trades.reason != "open")].sort_values("exit_time")
    for _, r in tr.iterrows():
        bucket += r.pnl_usd
        min_bucket = min(min_bucket, bucket)
        while bucket >= threshold:
            btc += threshold * (1 - spot_fee) / r.exit
            bucket -= threshold
            buys.append((r.exit_time, r.exit))
    last = float(d1h.close.iloc[-1])
    eq = tr.pnl_usd.cumsum()
    return dict(trades=len(tr), win=(tr.pnl_usd > 0).mean() if len(tr) else np.nan,
                adds=int(tr.added.sum()), add_wins=int((tr.added & (tr.pnl_usd > 0)).sum()),
                perp_net=tr.pnl_usd.sum(), perp_dd=float((eq.cummax().clip(lower=0) - eq).max()) if len(eq) else 0.0,
                cash_needed=-min_bucket, buys=len(buys), extra_btc=btc - btc0, btc=btc, btc0=btc0,
                value=btc * last + bucket, hold_value=btc0 * last, bucket=bucket, start_px=px0, last_px=last,
                buy_list=buys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-usd", type=float, default=1000.0)
    ap.add_argument("--thresholds", default="1000,100")
    ap.add_argument("--spot-fee", type=float, default=0.006, help="Coinbase spot fee per buy (0.6%% default)")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = copy.deepcopy(load_config(a.config))
    cfg["structure"]["pivot_lookback"] = BEST["pivot"]
    cfg["htf"]["map"]["1h"], cfg["htf"]["ema_length"] = BEST["htf_tf"], BEST["ema"]
    lay = {k: False for k in LAYERS}
    lay.update(BEST["layers"])
    upp, cost = dollars(cfg, "BTC")                  # 1 contract
    data = HERE / "data" / "reversal"
    d1h, d4h, d15 = load(data, "BTCUSD", "1h"), load(data, "BTCUSD", "4h"), load(data, "BTCUSD", "15m")
    tops = run(prepare(d1h, "1h", d4h, cfg), cfg, lay, signals_only=True)
    p15 = prepare(d15, "15m", None, cfg)

    variants = {"3:1, add 1 contract at 50% to stop": 0.5, "3:1, no add": None}
    starts = {"Mar 2017 (full history)": d1h.index[0] + pd.Timedelta(days=60),
              "Jan 2024 (unseen years only)": pd.Timestamp("2024-01-01", tz="UTC")}
    thresholds = [float(x) for x in a.thresholds.split(",")]
    lines = [f"# BTC stack + perp reversal shorts (start ${a.start_usd:,.0f} of BTC, held forever)", "",
             f"Shorts on top-reversal signals ({len(tops)} in total), 1 nano perp contract "
             f"(0.01 BTC, ${cost:.2f} fees+slippage per contract round trip), stop above the 1h lower high, "
             f"3:1 target. Perp profit buys spot BTC each time it reaches the threshold (spot fee "
             f"{a.spot_fee:.1%}). Funding payments not included.", ""]
    logs = []
    for vname, add_at in variants.items():
        tr = short_trades(tops, p15, cfg, 3.0, add_at, upp, cost)
        logs.append(tr.assign(variant=vname))
        for sname, s in starts.items():
            lines += [f"## {vname} — start {sname}", "",
                      "| buy BTC every | trades | win rate | adds (won) | perp net | perp max drawdown | "
                      "cash needed | BTC buys | BTC: start → end | end value | hold-only value |",
                      "|---|---|---|---|---|---|---|---|---|---|---|"]
            for th in thresholds:
                r = ledger(tr, d1h, s, a.start_usd, th, a.spot_fee)
                lines.append(
                    f"| ${th:,.0f} profit | {r['trades']} | {r['win']:.0%} | {r['adds']} ({r['add_wins']}) | "
                    f"{money(r['perp_net'])} | {money(r['perp_dd'])} | {money(r['cash_needed'])} | {r['buys']} | "
                    f"{r['btc0']:.5f} → {r['btc']:.5f} | {money(r['value'])} | {money(r['hold_value'])} |")
            lines.append("")
    lg = pd.concat(logs)
    for col in ("signal_time", "add_time", "exit_time"):
        lg[col] = pd.to_datetime(lg[col], utc=True).dt.tz_convert(ET).dt.strftime("%Y-%m-%d %I:%M %p")
    lg.round(2).to_csv(HERE / "stack_trades.csv", index=False)
    lines.append("Trade log (New York time): backtest/stack_trades.csv. 'Cash needed' = the most the perp "
                 "account was down in total, i.e. money you would have to keep ready for margin and losses.")
    (HERE / "stack_strategy.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
