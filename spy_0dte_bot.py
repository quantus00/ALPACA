#!/usr/bin/env python3
"""SPY 0DTE call+put bot (Alpaca, paper by default).

Strategy
    • At 10:34 ET, buy 1 SPY 0DTE CALL that is `--itm` strikes in-the-money
      (strike = round(spot) - itm) and 1 SPY 0DTE PUT that is `--itm` strikes
      in-the-money (strike = round(spot) + itm).
    • While open, watch the COMBINED profit of the two legs. When it reaches
      `--target` (default +30%), close both.
    • Hard stop: at 11:10 ET, close anything still open.

Broker: Alpaca. Uses the paper endpoint by default — fake money. It only sends
live orders if you point ALPACA_BASE_URL at the live API AND pass --live.

Setup
    pip install requests
    # in .env (or the environment):
    ALPACA_API_KEY=...        ALPACA_API_SECRET=...
    ALPACA_BASE_URL=https://paper-api.alpaca.markets   # paper (default)

Usage
    python spy_0dte_bot.py plan     # show what it would trade right now, no orders
    python spy_0dte_bot.py run      # run the scheduled strategy (paper)
    python spy_0dte_bot.py run --dry-run     # schedule + select, but never order
    python spy_0dte_bot.py run --itm 3 --target 0.30 --entry 10:34 --exit 11:10

0DTE options are extremely risky and can lose 100% fast. Paper-trade first.
Not financial advice.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
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
    target: float = 0.30
    itm: int = 3
    qty: int = 1
    poll: int = 15
    dry_run: bool = False
    live: bool = False


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
        """Nearest listed 0DTE contract of opt_type to `strike`."""
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

    def positions(self) -> list[dict]:
        return self._get(f"{self.base}/v2/positions")

    def close_position(self, symbol: str) -> None:
        r = requests.delete(f"{self.base}/v2/positions/{symbol}", headers=self.h, timeout=20)
        if r.status_code not in (200, 207):
            r.raise_for_status()


# --------------------------------------------------------------------------
def select_strikes(spot: float, itm: int) -> tuple[int, int]:
    """(call_strike, put_strike) each `itm` strikes in-the-money. SPY = $1 grid."""
    atm = round(spot)
    return atm - itm, atm + itm


def combined_pl(positions: list[dict], symbols: set[str]) -> tuple[float, float]:
    """Return (combined_pl_pct, combined_unrealized_pl_$) for our two legs."""
    cost = pl = 0.0
    for p in positions:
        if p["symbol"] in symbols:
            cost += abs(float(p["cost_basis"]))
            pl += float(p["unrealized_pl"])
    pct = pl / cost if cost else 0.0
    return pct, pl


def resolve_legs(api: Alpaca, cfg: Config):
    spot = api.spy_price()
    call_k, put_k = select_strikes(spot, cfg.itm)
    today = date.today().isoformat()
    call = api.find_contract("call", call_k, today)
    put = api.find_contract("put", put_k, today)
    return spot, call, put


# --------------------------------------------------------------------------
def cmd_plan(api: Alpaca, cfg: Config) -> int:
    spot, call, put = resolve_legs(api, cfg)
    log(f"SPY spot ≈ {spot:.2f}  →  ATM {round(spot)}")
    log(f"CALL {cfg.itm} ITM: {call['symbol']}  strike {call['strike_price']}")
    log(f"PUT  {cfg.itm} ITM: {put['symbol']}  strike {put['strike_price']}")
    log(f"Would buy {cfg.qty}x each at market; target +{cfg.target:.0%}; "
        f"hard exit {cfg.exit} ET. (plan only — no orders)")
    return 0


def cmd_run(api: Alpaca, cfg: Config) -> int:
    if not api.is_paper and not cfg.live:
        raise SystemExit("ALPACA_BASE_URL is a LIVE endpoint. Re-run with --live to allow real orders.")
    if not api.is_paper:
        log("⚠️  LIVE trading endpoint — real money.")

    now = datetime.now(ET)
    eh, em = map(int, cfg.entry.split(":"))
    xh, xm = map(int, cfg.exit.split(":"))
    entry_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    exit_dt = now.replace(hour=xh, minute=xm, second=0, microsecond=0)

    if now >= exit_dt:
        raise SystemExit(f"It's past the {cfg.exit} ET hard-exit for today. Nothing to do.")

    # Wait for entry (or enter now if we started mid-window).
    if now < entry_dt:
        wait = (entry_dt - now).total_seconds()
        log(f"Waiting {int(wait)}s until entry at {cfg.entry} ET…")
        time.sleep(wait)
    else:
        log(f"Started after {cfg.entry} ET — entering now.")

    spot, call, put = resolve_legs(api, cfg)
    symbols = {call["symbol"], put["symbol"]}
    log(f"SPY {spot:.2f} → CALL {call['symbol']} / PUT {put['symbol']}")

    if cfg.dry_run:
        log("[DRY RUN] Would buy both legs now; skipping orders and monitoring.")
        return 0

    for c in (call, put):
        o = api.buy_to_open(c["symbol"], cfg.qty)
        log(f"BUY {cfg.qty}x {c['symbol']} → order {o.get('id', '?')} ({o.get('status')})")

    # Monitor
    log(f"Monitoring: close both at +{cfg.target:.0%} combined, or at {cfg.exit} ET.")
    while True:
        now = datetime.now(ET)
        if now >= exit_dt:
            log(f"{cfg.exit} ET hard stop — closing both.")
            break
        try:
            pct, pl = combined_pl(api.positions(), symbols)
            log(f"combined P/L {pct:+.1%}  (${pl:+.2f})")
            if pct >= cfg.target:
                log(f"🎯 target hit (+{cfg.target:.0%}) — closing both.")
                break
        except requests.HTTPError as e:
            log(f"poll error: {e}")
        time.sleep(cfg.poll)

    for sym in symbols:
        try:
            api.close_position(sym)
            log(f"CLOSE {sym} submitted.")
        except requests.HTTPError as e:
            log(f"close {sym} failed (already flat?): {e}")
    log("Done.")
    return 0


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="SPY 0DTE call+put bot (Alpaca).")
    ap.add_argument("cmd", choices=["run", "plan"], help="run the strategy, or preview selection")
    ap.add_argument("--entry", default="10:34", help="entry time ET (HH:MM)")
    ap.add_argument("--exit", default="11:10", help="hard-close time ET (HH:MM)")
    ap.add_argument("--target", type=float, default=0.30, help="combined take-profit, e.g. 0.30")
    ap.add_argument("--itm", type=int, default=3, help="strikes in-the-money for each leg")
    ap.add_argument("--qty", type=int, default=1, help="contracts per leg")
    ap.add_argument("--poll", type=int, default=15, help="seconds between P/L checks")
    ap.add_argument("--dry-run", action="store_true", help="select + schedule but never send orders")
    ap.add_argument("--live", action="store_true", help="allow orders against a LIVE endpoint")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)

    cfg = Config(entry=a.entry, exit=a.exit, target=a.target, itm=a.itm,
                 qty=a.qty, poll=a.poll, dry_run=a.dry_run, live=a.live)
    api = Alpaca()
    try:
        return cmd_plan(api, cfg) if a.cmd == "plan" else cmd_run(api, cfg)
    except requests.HTTPError as e:
        print(f"Alpaca API error: {e}  {getattr(e.response, 'text', '')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted — check your open positions manually.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
