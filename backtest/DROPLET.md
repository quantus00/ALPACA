# Running the MES backtest on your DigitalOcean droplet

The engine is **pure Python 3.9+ — no pip installs, no API keys** to run on the
bundled data snapshot. Fetching *fresh* data is optional (see step 3).

## 1. Get the code onto the droplet

```bash
cd /opt
git clone -b claude/multi-timeframe-trend-bot-lqynuy https://github.com/quantus00/ALPACA.git   # or: cd /opt/ALPACA && git pull
cd /opt/ALPACA
python3 --version   # need 3.9+ ; if zoneinfo errors: pip install tzdata
```

## 2. Run the backtest and get results

```bash
# All timeframes, all variants -> prints a table + writes results.txt
python3 backtest/mes_backtest.py

# Pick timeframes and export a per-trade CSV (entry/exit, reason, net $)
python3 backtest/mes_backtest.py --tf M15,M30 --csv backtest/trades.csv

# Options
python3 backtest/mes_backtest.py --help
#   --data-dir DIR   where the MESZ6_<TF>.json files live (default backtest/data)
#   --tf LIST        comma list: M5,M15,M30,M60
#   --trend-tf TF    which dataset defines the master 4H trend (default M60)
#   --csv PATH       write a per-trade CSV
#   --out PATH       where to write the summary table (default backtest/results.txt)
```

Variants tested: `opt1_30_10` (1 contract 30/10 baseline), `opt2a_60_20`,
`opt2b_30_10`, `opt2c_3_1` (all: add 7 at +20 pt → 8 contracts, then that
bracket from the blended average).

## 3. (Optional) Refresh the data from your own source

The bundled snapshot is a one-time pull (Sep 2026). To backtest fresh bars,
point `fetch_data.py` at any REST endpoint that returns OHLC bars — **your ICC
Cockpit app**, a Webull OpenAPI proxy, etc.:

```bash
export ICC_BARS_URL='https://your-icc-cockpit.app/api/bars?symbol={symbol}&tf={tf}&count={count}'
export ICC_API_KEY='...'                 # optional, sent as Bearer token
# If your JSON uses different field names, map them:
export ICC_FIELD_MAP='{"time":"t","open":"o","high":"h","low":"l","close":"c","volume":"v"}'
# If the bars are nested, give the path: e.g. data.candles
export ICC_ROWS_PATH='data.candles'

python3 backtest/fetch_data.py --symbol MESZ6 --tf M5,M15,M30,M60 --count 1200
python3 backtest/mes_backtest.py          # now runs on the fresh data
```

The backtest reads one JSON file per timeframe (`backtest/data/<SYMBOL>_<TF>.json`)
in this shape:

```json
[ { "result": [
  {"time":"2026-09-24T06:50:00.000+0000","open":"7738.5","high":"7739.25",
   "low":"7735.25","close":"7735.75","volume":"1101"} ] } ]
```

Any source that can produce that (or be remapped to it via `ICC_FIELD_MAP`)
works.

## Notes / caveats
- $5/point MES, $0.62/contract/side commission, RTH-only entries, conservative
  fills (stop-before-target on ties). See `results.md`.
- Small samples (6–28 trades over 1–10 weeks). Treat as a mechanics check, not a
  validated edge.
