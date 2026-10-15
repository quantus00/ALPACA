"""The only seam between the existing bot and the hedge module.

Enabled with BOT_HEDGE_PAPER=true (paper; live also needs
live_trading_approved=true in hedge_config.toml AND the bot run with --live) and only for BTC spot (the crypto primary).
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
    from . import config as hcfg
    from .live import LiveExecutor, live_allowed
    from .paper import PaperHedgeManager
    s = hcfg.load("crypto")
    executor = None
    if live_allowed(cfg, s):
        log.warning("LIVE hedge orders ENABLED (live_trading_approved=true and --live)")
        executor = LiveExecutor(cfg, s, notifier=notifier)
    elif s.live_trading_approved:
        log.warning("live_trading_approved=true but bot is dry-run -> hedge stays PAPER")
    return PaperHedgeManager(primary_symbol=cfg.symbol(), notifier=notifier,
                             primary_units=cfg.contract_size, executor=executor)
