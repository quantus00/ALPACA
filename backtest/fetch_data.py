"""
Refresh the MES bar data the backtest reads, from an HTTP source you control
(your ICC Cockpit app, a Webull OpenAPI proxy, or any REST endpoint).

The backtest (`mes_backtest.py`) reads one JSON file per timeframe with this
exact shape (newest or oldest order is fine — the loader sorts):

    [ { "result": [
          {"time":"2026-09-24T06:50:00.000+0000",
           "open":"7738.5","high":"7739.25","low":"7735.25",
           "close":"7735.75","volume":"1101"},
          ... ] } ]

This script fetches rows from your endpoint, normalizes the field names to that
shape, and writes backtest/data/<SYMBOL>_<TF>.json.

CONFIGURE (env vars):
  ICC_BARS_URL   URL template, e.g.
                 https://your-icc-cockpit.app/api/bars?symbol={symbol}&tf={tf}&count={count}
  ICC_API_KEY    optional; sent as  Authorization: Bearer <key>
  ICC_FIELD_MAP  optional JSON remapping your field names to
                 time/open/high/low/close/volume, e.g.
                 '{"time":"t","open":"o","high":"h","low":"l","close":"c","volume":"v"}'
  ICC_ROWS_PATH  optional dotted path to the array in the response
                 (e.g. "data.candles"); default: auto-detect a list.

USAGE:
  python3 backtest/fetch_data.py --symbol MESZ6 --tf M5,M15,M30,M60 --count 1200

If ICC_BARS_URL is not set, this prints the schema and exits — the backtest
still runs on the bundled snapshot in backtest/data/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = {"time": "time", "open": "open", "high": "high",
               "low": "low", "close": "close", "volume": "volume"}


def _dig(obj, path):
    if not path:
        # auto-detect: first list of dicts we find
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                got = _dig(v, "")
                if isinstance(got, list) and got and isinstance(got[0], dict):
                    return got
        return obj
    for key in path.split("."):
        obj = obj[key]
    return obj


def normalize(rows: list[dict], fmap: dict) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "time": str(r[fmap["time"]]),
            "open": str(r[fmap["open"]]),
            "high": str(r[fmap["high"]]),
            "low": str(r[fmap["low"]]),
            "close": str(r[fmap["close"]]),
            "volume": str(r.get(fmap["volume"], 0)),
        })
    return out


def fetch(url: str, api_key: str | None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "mes-backtest/1.0"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    ap = argparse.ArgumentParser(description="Refresh MES bar data for the backtest")
    ap.add_argument("--symbol", default="MESZ6")
    ap.add_argument("--tf", default="M5,M15,M30,M60")
    ap.add_argument("--count", type=int, default=1200)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    args = ap.parse_args()

    url_tmpl = os.getenv("ICC_BARS_URL")
    if not url_tmpl:
        print(__doc__)
        print("ICC_BARS_URL not set — nothing fetched. The backtest still runs "
              "on the bundled snapshot.")
        sys.exit(0)

    api_key = os.getenv("ICC_API_KEY")
    rows_path = os.getenv("ICC_ROWS_PATH", "")
    fmap = dict(DEFAULT_MAP)
    if os.getenv("ICC_FIELD_MAP"):
        fmap.update(json.loads(os.environ["ICC_FIELD_MAP"]))

    os.makedirs(args.data_dir, exist_ok=True)
    for tf in [t.strip() for t in args.tf.split(",") if t.strip()]:
        url = url_tmpl.format(symbol=args.symbol, tf=tf, timespan=tf, count=args.count)
        try:
            payload = fetch(url, api_key)
            rows = normalize(_dig(payload, rows_path), fmap)
            if not rows:
                print(f"{tf}: no rows returned")
                continue
            path = os.path.join(args.data_dir, f"{args.symbol}_{tf}.json")
            json.dump([{"result": rows}], open(path, "w"))
            print(f"{tf}: wrote {len(rows)} bars -> {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"{tf}: FETCH FAILED: {exc}")


if __name__ == "__main__":
    main()
