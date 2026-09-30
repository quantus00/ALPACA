# MES Backtest

Real-data backtest of the "Open + Trend + Pullback" MES strategy and the
scale-in exit variants.

- `mes_backtest.py` — the engine (fetch-free; reads `data/*.json`).
- `data/MESZ6_*.json` — MESZ6 bars (M5/M15/M30/M60) pulled from Webull 2026-09-24.
- `results.md` — written-up results + caveats.  `results.txt` — raw table.

Run:

    python3 backtest/mes_backtest.py

The 4H trend is derived from the M60 series and applied to every entry
timeframe. See `results.md` for the important sample-size and fill-model caveats.
