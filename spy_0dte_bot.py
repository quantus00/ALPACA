#!/usr/bin/env python3
"""SPY 0DTE call+put bot with an interactive exit console (Alpaca paper).

Entry
    At 10:34 ET (‑‑entry), buy 1 SPY 0DTE CALL `--itm` strikes in-the-money
    (strike = round(spot) - itm) and 1 SPY 0DTE PUT `--itm` strikes ITM
    (strike = round(spot) + itm).

Managing the trade — YOU choose how to close
    Once both legs are open a live console shows CALL / PUT / COMBINED P/L and
    lets you act each cycle:
        cb          close BOTH legs and quit
        cc / cp     close just the Call / just the Put
        bm          buy more (add contracts to a leg)
        tp <pct>    set/replace the COMBINED take-profit  (e.g. tp 0.30)
        ctp <pct>   set the CALL-leg take-profit          (e.g. ctp 0.5)
        ptp <pct>   set the PUT-leg take-profit
        sl <pct>    set a COMBINED stop-loss              (e.g. sl -0.5)
        off <t|c|p|s>   disable a rule (target / call / put / stop)
        st          reprint status   ·   q   close all and quit
    Any armed rule auto-fires even while you're away. Hard time-stop at 11:10 ET
    (--exit) always closes everything.

Broker: Alpaca, paper by default (fake money). Live orders require a live
ALPACA_BASE_URL *and* --live.

Setup
    pip install requests
    # .env:  ALPACA_API_KEY=...  ALPACA_API_SECRET=...
    #        ALPACA_BASE_URL=https://paper-api.alpaca.markets

Usage
    python spy_0dte_bot.py plan                 # preview strikes, no orders
    python spy_0dte_bot.py run                  # entry + interactive console
    python spy_0dte_bot.py run --target 0.30 --stop -0.5 --exit 11:10
    python spy_0dte_bot.py run --auto           # no prompts: rules + time only

0DTE options are extremely risky. Paper-trade first. Not financial advice.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    print("Python 3.9+ required (zoneinfo).", file=sys.stderr)
    raise

import requests

ET = ZoneInfo("America/New_York")
DATA_URL = "https://data.alpaca.markets"


# --------------------------------------------------------------------------
def load_dotenv(path: str | os.PathLike = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def log(msg: str) -> None:
    print(f"[{datetime.now(ET):%H:%M:%S} ET] {msg}", flush=True)


# --------------------------------------------------------------------------
@dataclass
class Config:
    entry: str = "10:34"
    exit: str = "11:10"
    target: float | None = 0.30          # combined take-profit
    call_target: float | None = None     # call-leg take-profit
    put_target: float | None = None      # put-leg take-profit
    stop: float | None = None            # combined stop-loss (negative)
    itm: int = 3
    qty: int = 1
    poll: int = 15
    dry_run: bool = False
    live: bool = False
    interactive: bool = True


class Alpaca:
    def __init__(self) -> None:
        self.key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_API_SECRET")
        self.base = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
        if not self.key or not self.secret:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_API_SECRET (env or .env).")
        self.h = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    @property
    def is_paper(self) -> bool:
        return "paper" in self.base

    def _get(self, url: str, **params):
        r = requests.get(url, headers=self.h, params=params or None, timeout=20)
        r.raise_for_status()
        return r.json()

    def spy_price(self) -> float:
        j = self._get(f"{DATA_URL}/v2/stocks/SPY/trades/latest", feed="iex")
        return float(j["trade"]["p"])

    def find_contract(self, opt_type: str, strike: float, expiration: str) -> dict:
        j = self._get(f"{self.base}/v2/options/contracts",
                      underlying_symbols="SPY", expiration_date=expiration,
                      type=opt_type, strike_price_gte=strike - 5,
                      strike_price_lte=strike + 5, limit=100)
        rows = j.get("option_contracts") or []
        if not rows:
            raise RuntimeError(f"No 0DTE {opt_type} contracts near {strike} for {expiration}")
        return min(rows, key=lambda c: abs(float(c["strike_price"]) - strike))

    def buy_to_open(self, symbol: str, qty: int) -> dict:
        r = requests.post(f"{self.base}/v2/orders", headers=self.h, timeout=20, json={
            "symbol": symbol, "qty": qty, "side": "buy",
            "type": "market", "time_in_force": "day"})
        r.raise_for_status()
        return r.json()

    def positions_by_symbol(self) -> dict[str, dict]:
        return {p["symbol"]: p for p in self._get(f"{self.base}/v2/positions")}

    def close_position(self, symbol: str) -> None:
        r = requests.delete(f"{self.base}/v2/positions/{symbol}", headers=self.h, timeout=20)
        if r.status_code not in (200, 207):
            r.raise_for_status()


# --------------------------------------------------------------------------
def select_strikes(spot: float, itm: int) -> tuple[int, int]:
    """(call_strike, put_strike) each `itm` strikes in-the-money. SPY = $1 grid."""
    atm = round(spot)
    return atm - itm, atm + itm


def resolve_legs(api: Alpaca, cfg: Config):
    spot = api.spy_price()
    call_k, put_k = select_strikes(spot, cfg.itm)
    today = date.today().isoformat()
    return spot, api.find_contract("call", call_k, today), api.find_contract("put", put_k, today)


# ---- interactive input (POSIX: timed; else blocking) ---------------------
def read_command(timeout: float, interactive: bool) -> str | None:
    if not interactive:
        time.sleep(timeout)
        return None
    try:
        import select
        print("cmd> ", end="", flush=True)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.readline().strip()
        print()  # newline after the idle prompt
        return None
    except Exception:  # Windows / non-tty: block on input, rules checked each loop
        try:
            return input("cmd> ").strip()
        except EOFError:
            time.sleep(timeout)
            return None


def _pct(tok: str) -> float | None:
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
def cmd_plan(api: Alpaca, cfg: Config) -> int:
    spot, call, put = resolve_legs(api, cfg)
    log(f"SPY spot ≈ {spot:.2f}  →  ATM {round(spot)}")
    log(f"CALL {cfg.itm} ITM: {call['symbol']}  strike {call['strike_price']}")
    log(f"PUT  {cfg.itm} ITM: {put['symbol']}  strike {put['strike_price']}")
    log("plan only — no orders.")
    return 0


def cmd_run(api: Alpaca, cfg: Config) -> int:
    if not api.is_paper and not cfg.live:
        raise SystemExit("ALPACA_BASE_URL is LIVE. Re-run with --live to allow real orders.")
    if not api.is_paper:
        log("⚠️  LIVE trading endpoint — real money.")

    now = datetime.now(ET)
    eh, em = map(int, cfg.entry.split(":"))
    xh, xm = map(int, cfg.exit.split(":"))
    entry_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    exit_dt = now.replace(hour=xh, minute=xm, second=0, microsecond=0)
    if now >= exit_dt:
        raise SystemExit(f"Past the {cfg.exit} ET hard-exit for today. Nothing to do.")

    if now < entry_dt:
        wait = (entry_dt - now).total_seconds()
        log(f"Waiting {int(wait)}s until entry at {cfg.entry} ET…")
        time.sleep(wait)
    else:
        log(f"Started after {cfg.entry} ET — entering now.")

    spot, call, put = resolve_legs(api, cfg)
    contracts = {"call": call["symbol"], "put": put["symbol"]}
    log(f"SPY {spot:.2f} → CALL {contracts['call']} / PUT {contracts['put']}")

    if cfg.dry_run:
        log("[DRY RUN] would buy both legs; skipping orders + console.")
        return 0

    for c in (call, put):
        o = api.buy_to_open(c["symbol"], cfg.qty)
        log(f"BUY {cfg.qty}x {c['symbol']} → order {o.get('id', '?')} ({o.get('status')})")

    manage(api, cfg, contracts, exit_dt)
    log("Done.")
    return 0


def manage(api: Alpaca, cfg: Config, contracts: dict[str, str], exit_dt: datetime) -> None:
    """Interactive management loop: live P/L + armed rules + your commands."""
    rules = {"target": cfg.target, "call": cfg.call_target,
             "put": cfg.put_target, "stop": cfg.stop}
    open_sides = {"call", "put"}

    def show_rules():
        parts = []
        for k, lbl in (("target", "combined TP"), ("call", "call TP"),
                       ("put", "put TP"), ("stop", "stop")):
            parts.append(f"{lbl}={rules[k]:+.0%}" if rules[k] is not None else f"{lbl}=off")
        log("rules: " + "  ".join(parts))

    def close_side(side: str):
        sym = contracts[side]
        try:
            api.close_position(sym)
            log(f"CLOSE {side.upper()} {sym} submitted.")
        except requests.HTTPError as e:
            log(f"close {side} failed (already flat?): {e}")
        open_sides.discard(side)

    log(f"Managing. Combined target {('+%.0f%%' % (cfg.target*100)) if cfg.target is not None else 'off'}, "
        f"hard exit {cfg.exit} ET.")
    show_rules()

    while open_sides:
        now = datetime.now(ET)
        try:
            pos = api.positions_by_symbol()
        except requests.HTTPError as e:
            log(f"poll error: {e}")
            time.sleep(cfg.poll)
            continue

        stat: dict[str, tuple[float, float]] = {}   # side -> (plpc, pl$)
        cost_all = pl_all = 0.0
        for side in list(open_sides):
            p = pos.get(contracts[side])
            if not p or abs(float(p.get("qty", 0))) < 1e-9:
                log(f"{side.upper()} leg is flat.")
                open_sides.discard(side)
                continue
            plpc = float(p["unrealized_plpc"])
            pl = float(p["unrealized_pl"])
            stat[side] = (plpc, pl)
            cost_all += abs(float(p["cost_basis"]))
            pl_all += pl
        if not open_sides:
            break
        combined = pl_all / cost_all if cost_all else 0.0
        legs_txt = "  ".join(f"{s.upper()} {stat[s][0]:+.1%} (${stat[s][1]:+.2f})" for s in stat)
        log(f"{legs_txt}  |  COMBINED {combined:+.1%} (${pl_all:+.2f})")

        # ---- armed auto-rules ------------------------------------------
        fire = None
        if now >= exit_dt:
            fire = ("both", f"time {cfg.exit}")
        elif rules["stop"] is not None and combined <= rules["stop"]:
            fire = ("both", f"combined stop {rules['stop']:+.0%}")
        elif rules["target"] is not None and combined >= rules["target"]:
            fire = ("both", f"combined +{rules['target']:.0%}")
        elif rules["call"] is not None and "call" in stat and stat["call"][0] >= rules["call"]:
            fire = ("call", f"call +{rules['call']:.0%}")
        elif rules["put"] is not None and "put" in stat and stat["put"][0] >= rules["put"]:
            fire = ("put", f"put +{rules['put']:.0%}")
        if fire:
            log(f"⚡ auto: {fire[1]} → close {fire[0]}")
            if fire[0] == "both":
                for s in list(open_sides):
                    close_side(s)
                break
            close_side(fire[0])
            continue

        # ---- your command ----------------------------------------------
        cmd = read_command(cfg.poll, cfg.interactive)
        if not cmd:
            continue
        parts = cmd.split()
        op = parts[0].lower()
        arg = _pct(parts[1]) if len(parts) > 1 else None

        if op in ("cb", "q", "close"):
            for s in list(open_sides):
                close_side(s)
            break
        elif op == "cc":
            close_side("call")
        elif op == "cp":
            close_side("put")
        elif op == "bm":
            _buy_more(api, cfg, contracts, open_sides)
        elif op == "tp" and arg is not None:
            rules["target"] = arg; show_rules()
        elif op == "ctp" and arg is not None:
            rules["call"] = arg; show_rules()
        elif op == "ptp" and arg is not None:
            rules["put"] = arg; show_rules()
        elif op == "sl" and arg is not None:
            rules["stop"] = arg; show_rules()
        elif op == "off" and len(parts) > 1:
            key = {"t": "target", "c": "call", "p": "put", "s": "stop"}.get(parts[1].lower())
            if key:
                rules[key] = None; show_rules()
        elif op == "st":
            show_rules()
        else:
            log("commands: cb cc cp bm | tp<x> ctp<x> ptp<x> sl<x> off<t|c|p|s> st q")


def _buy_more(api: Alpaca, cfg: Config, contracts: dict[str, str], open_sides: set[str]) -> None:
    try:
        side = input("  buy more which leg? [call/put/both]: ").strip().lower()
        n = input(f"  how many contracts? [{cfg.qty}]: ").strip()
        qty = int(n) if n else cfg.qty
    except EOFError:
        return
    sides = ["call", "put"] if side in ("both", "b") else [side] if side in ("call", "put") else []
    if not sides:
        log("  cancelled.")
        return
    for s in sides:
        try:
            o = api.buy_to_open(contracts[s], qty)
            open_sides.add(s)
            log(f"  BUY {qty}x {contracts[s]} → order {o.get('id', '?')} ({o.get('status')})")
        except requests.HTTPError as e:
            log(f"  buy {s} failed: {e}")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="SPY 0DTE call+put bot with interactive exits (Alpaca).")
    ap.add_argument("cmd", choices=["run", "plan"])
    ap.add_argument("--entry", default="10:34", help="entry time ET (HH:MM)")
    ap.add_argument("--exit", default="11:10", help="hard-close time ET (HH:MM)")
    ap.add_argument("--target", default="0.30", help="combined take-profit (e.g. 0.30) or 'off'")
    ap.add_argument("--call-target", default="off", help="call-leg take-profit or 'off'")
    ap.add_argument("--put-target", default="off", help="put-leg take-profit or 'off'")
    ap.add_argument("--stop", default="off", help="combined stop-loss, e.g. -0.5, or 'off'")
    ap.add_argument("--itm", type=int, default=3, help="strikes in-the-money per leg")
    ap.add_argument("--qty", type=int, default=1, help="contracts per leg")
    ap.add_argument("--poll", type=int, default=15, help="seconds between refreshes")
    ap.add_argument("--auto", action="store_true", help="no prompts: run armed rules + time only")
    ap.add_argument("--dry-run", action="store_true", help="select + schedule, never order")
    ap.add_argument("--live", action="store_true", help="allow orders against a LIVE endpoint")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)

    def opt(v):
        return None if str(v).lower() in ("off", "none", "") else float(v)

    cfg = Config(entry=a.entry, exit=a.exit, target=opt(a.target),
                 call_target=opt(a.call_target), put_target=opt(a.put_target),
                 stop=opt(a.stop), itm=a.itm, qty=a.qty, poll=a.poll,
                 dry_run=a.dry_run, live=a.live, interactive=not a.auto)
    api = Alpaca()
    try:
        return cmd_plan(api, cfg) if a.cmd == "plan" else cmd_run(api, cfg)
    except requests.HTTPError as e:
        print(f"Alpaca API error: {e}  {getattr(e.response, 'text', '')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted — check open positions manually.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
