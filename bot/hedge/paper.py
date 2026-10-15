"""Paper-trading hedge manager — the piece that plugs into the live bot.

The existing ICC logic (``StrategyRunner``) still decides and places the primary
entry. After a primary BUY fills, ``bot/main.py`` calls ``on_primary_entry``;
on every poll tick it calls ``on_tick``. This class pulls closed 1h candles,
runs them through the shared ``HedgeEngine`` and writes every event (hedge
open / skip / lift / cut / limit close / funding) with its reason to the log and
to a JSONL file.

PAPER ONLY. Hedge fills are simulated at the 1h close; no hedge order is ever
sent to a broker. ``_send_live`` exists only to make that explicit.

Crypto only: no broker wired into this bot can trade EUR/USD and USD/CHF, and
the bot has no FX candle feed, so the forex hedge is backtest-only.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict

from ..data import get_candles
from . import config as hcfg
from .engine import Event, HedgeEngine
from .features import FeatureBuilder

log = logging.getLogger("hedge")

WARMUP_BARS = 300


class PaperHedgeManager:
    def __init__(self, primary_symbol: str = "BTC-USD", notifier=None,
                 config_path: str | None = None, candle_fn=get_candles) -> None:
        self.s = hcfg.load("crypto", config_path)
        self.symbol = primary_symbol
        self.notifier = notifier
        self.candle_fn = candle_fn
        self.engine = HedgeEngine(self.s, mode="hedge")
        self.features: FeatureBuilder | None = None
        self.last_ctx = None
        self.last_bar_time: int | None = None
        os.makedirs(os.path.dirname(self.s.paper_log_path) or ".", exist_ok=True)
        log.info("[PAPER] hedge module active for %s -> hedge %s (log %s)",
                 self.symbol, self.s.hedge_symbol, self.s.paper_log_path)

    # ----------------------------------------------------------------- feed
    def _closed_bars(self):
        bars = self.candle_fn(self.symbol, "1h", limit=WARMUP_BARS)
        now = time.time()
        return [b for b in bars if b.time + 3600 <= now]   # drop the forming bar

    def _sync(self) -> None:
        bars = self._closed_bars()
        if self.features is None:
            self.features = FeatureBuilder(self.s.atr_length, self.s.pivot_lookback)
            now = time.time()
            h4 = [b for b in self.candle_fn(self.symbol, "4h", limit=WARMUP_BARS)
                  if b.time + 4 * 3600 <= now]
            self.features.seed_4h(h4)
            for b in bars:
                self.last_ctx = self.features.update(b)
                self.last_bar_time = b.time
            return
        for b in bars:
            if self.last_bar_time is not None and b.time <= self.last_bar_time:
                continue
            self.last_ctx = self.features.update(b)
            self.last_bar_time = b.time
            self._record(self.engine.on_bar(self.last_ctx))

    # ----------------------------------------------------------------- hooks
    def on_primary_entry(self, side: str, price: float | None = None) -> None:
        """Hook: the existing ICC logic just entered. Only BTC spot longs are hedged."""
        if side != "buy":
            log.info("[PAPER] hedge module ignores %s (crypto primary is spot LONG only)", side)
            return
        if not self.engine.is_flat:
            log.info("[PAPER] setup already open — one hedge per primary, not stacking")
            return
        self._sync()
        if self.last_ctx is None or self.last_ctx.atr is None:
            log.warning("[PAPER] not enough 1h history for ATR; hedge not armed")
            return
        self._record(self.engine.open_primary(self.last_ctx, side=1))

    def on_tick(self) -> None:
        """Hook: called every poll; processes any newly closed 1h bars. Primary
        closes decided here (stop / cut / limit) are paper ledger events too."""
        self._sync()

    # ----------------------------------------------------------------- output
    def _record(self, events: list[Event]) -> None:
        for ev in events:
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ev.time)),
                   "mode": "PAPER", "pair": "crypto", **asdict(ev)}
            with open(self.s.paper_log_path, "a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            if ev.kind == "FUNDING":
                log.debug("[PAPER] %s %s", ev.kind, ev.data)
                continue
            msg = f"[PAPER] setup#{ev.setup_id} {ev.kind}: {ev.reason}"
            log.info("%s %s", msg, ev.data)
            if self.notifier and ev.kind != "PRIMARY_OPEN":
                try:
                    self.notifier._push(msg)
                except Exception:  # noqa: BLE001
                    log.warning("hedge notify failed", exc_info=True)

    def _send_live(self, *_a, **_k):  # pragma: no cover - deliberately unreachable
        raise NotImplementedError(
            "Live hedge orders are disabled. The hedge module is paper-only until "
            "the owner approves live trading and a live path is reviewed.")
