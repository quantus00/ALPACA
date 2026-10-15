"""The only seam between the existing bot and the hedge module.

Enabled with BOT_HEDGE_PAPER=true and only for BTC spot (the crypto primary).
Returns None otherwise, so the existing bot behaves exactly as before.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("hedge")


def make_hedge_hook(cfg, notifier=None):
    if os.getenv("BOT_HEDGE_PAPER", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    from ..config import Instrument
    if cfg.instrument != Instrument.BTC_USD_SPOT:
        log.warning("BOT_HEDGE_PAPER set but instrument is %s; hedge module only "
                    "pairs BTC spot (forex hedge is backtest-only).", cfg.instrument.value)
        return None
    from .paper import PaperHedgeManager
    return PaperHedgeManager(primary_symbol=cfg.symbol(), notifier=notifier)
