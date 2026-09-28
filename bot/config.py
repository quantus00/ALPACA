"""Central configuration: broker / instrument / contract-size toggles.

Everything the operator flips lives here (or in the environment). The rest of
the bot reads from a single ``Config`` instance so the toggles are the only
place behaviour changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Broker(str, Enum):
    COINBASE = "coinbase"
    ALPACA = "alpaca"
    TRADOVATE = "tradovate"
    WEBULL = "webull"


class Instrument(str, Enum):
    BTC_USD_SPOT = "btc_usd_spot"     # Coinbase spot
    BTC_NANO_PERP = "btc_nano_perp"   # Coinbase nano BTC perpetual future
    SPY_OPTIONS = "spy_options"       # Alpaca / Webull options
    MES_FUTURES = "mes_futures"       # Tradovate Micro E-mini S&P 500


class AssetClass(str, Enum):
    """Free-form asset classes for the Alex strategy path (``bot.main alex``).

    Unlike :class:`Instrument` (a fixed set of hand-wired products), an asset
    class + a free-form symbol lets the bot trade *any* asset the broker
    supports (all Coinbase spot pairs, any Webull futures/crypto), which is what
    the Alex market-structure bot needs.
    """
    CRYPTO = "crypto"
    FUTURES = "futures"
    STOCK = "stock"
    OPTION = "option"


# In free-symbol (Alex) mode, which brokers may trade each asset class.
ASSET_BROKERS: dict[AssetClass, tuple[Broker, ...]] = {
    AssetClass.CRYPTO: (Broker.COINBASE, Broker.WEBULL),
    AssetClass.FUTURES: (Broker.WEBULL, Broker.TRADOVATE),
    AssetClass.STOCK: (Broker.WEBULL, Broker.ALPACA),
    AssetClass.OPTION: (Broker.WEBULL, Broker.ALPACA),
}


# Which brokers can trade which instrument. Used to validate the toggles.
INSTRUMENT_BROKERS: dict[Instrument, tuple[Broker, ...]] = {
    Instrument.BTC_USD_SPOT: (Broker.COINBASE,),
    Instrument.BTC_NANO_PERP: (Broker.COINBASE,),
    Instrument.SPY_OPTIONS: (Broker.ALPACA, Broker.WEBULL),
    Instrument.MES_FUTURES: (Broker.TRADOVATE,),
}

# Default tradable symbol per instrument, per broker.
INSTRUMENT_SYMBOLS: dict[Instrument, str] = {
    Instrument.BTC_USD_SPOT: "BTC-USD",
    Instrument.BTC_NANO_PERP: "BTC-PERP-INTX",
    Instrument.SPY_OPTIONS: "SPY",
    Instrument.MES_FUTURES: "MES",
}

# Contract multiplier per instrument, used to weight each leg's dollar P/L when
# combining legs of different notional (options are quoted per-share x100).
INSTRUMENT_MULTIPLIER: dict[Instrument, float] = {
    Instrument.BTC_USD_SPOT: 1.0,
    Instrument.BTC_NANO_PERP: 1.0,
    Instrument.SPY_OPTIONS: 100.0,
    Instrument.MES_FUTURES: 5.0,
}

# Point value ($ per 1.0 price move per contract) for common Webull micro/mini
# futures roots, used to weight futures P/L in free-symbol (Alex) mode. Unknown
# roots fall back to 1.0. Matched by the leading letters of the symbol root.
_FUTURES_MULTIPLIER: dict[str, float] = {
    "MES": 5.0, "ES": 50.0,       # Micro / E-mini S&P 500
    "MNQ": 2.0, "NQ": 20.0,       # Micro / E-mini Nasdaq-100
    "M2K": 5.0, "RTY": 50.0,      # Micro / E-mini Russell 2000
    "MYM": 0.5, "YM": 5.0,        # Micro / E-mini Dow
    "MGC": 10.0, "GC": 100.0,     # Micro / full Gold
    "MCL": 100.0, "CL": 1000.0,   # Micro / full Crude Oil
    "MBT": 0.1, "BTC": 5.0,       # Micro / Bitcoin futures
}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_asset_class() -> "AssetClass | None":
    raw = os.getenv("BOT_ASSET_CLASS", "").strip().lower()
    if not raw:
        return None
    try:
        return AssetClass(raw)
    except ValueError:
        return None


@dataclass
class Config:
    # ---- Primary toggles -----------------------------------------------------
    broker: Broker = Broker(os.getenv("BOT_BROKER", "coinbase"))
    instrument: Instrument = Instrument(os.getenv("BOT_INSTRUMENT", "btc_usd_spot"))

    # ---- Free-symbol (Alex strategy) mode -----------------------------------
    # When ``symbol_override`` and ``asset_class`` are both set, the bot trades
    # that free-form symbol in that asset class instead of the fixed Instrument
    # enum above. This is how ``bot.main alex`` trades any asset the broker has.
    symbol_override: str = os.getenv("BOT_SYMBOL", "")
    asset_class: "AssetClass | None" = field(default_factory=_env_asset_class)
    # Which Webull integration the factory builds: "openapi" (official Webull
    # OpenAPI SDK; crypto/futures/stock) or "community" (unofficial webull pip).
    webull_backend: str = os.getenv("WEBULL_BACKEND", "openapi")

    # Contract / order size toggle. Meaning depends on the instrument:
    #   - crypto spot/perp : base-asset quantity (e.g. 0.01 BTC)
    #   - options          : number of option contracts
    #   - futures          : number of futures contracts
    contract_size: float = float(os.getenv("BOT_CONTRACT_SIZE", "1"))

    # ---- Structure / strategy params ----------------------------------------
    pivot_lookback: int = int(os.getenv("BOT_PIVOT_LOOKBACK", "5"))
    trend_timeframe: str = os.getenv("BOT_TREND_TF", "4h")
    alignment_timeframes: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            os.getenv("BOT_ALIGN_TFS", "5m,15m,30m,1h").split(",")
        )
    )

    # ---- Options-specific knobs ---------------------------------------------
    option_dte: int = int(os.getenv("BOT_OPTION_DTE", "0"))          # target days-to-expiry
    option_delta_target: float = float(os.getenv("BOT_OPTION_DELTA", "0.40"))

    # ---- Dual-broker P/L exits ----------------------------------------------
    # Percentages; 0 disables that rule. Combined = the whole basket; leg_* =
    # each broker's own position watched independently. Any rule that trips
    # flattens *every* open leg at once.
    tp_pct: float = float(os.getenv("BOT_TP_PCT", "0"))       # combined take-profit
    sl_pct: float = float(os.getenv("BOT_SL_PCT", "0"))       # combined stop-loss (magnitude)
    leg_tp_pct: float = float(os.getenv("BOT_LEG_TP_PCT", "0"))
    leg_sl_pct: float = float(os.getenv("BOT_LEG_SL_PCT", "0"))
    # How a tripped *per-leg* rule closes: "combined" flattens every leg
    # together; "single" flattens only the leg that hit its threshold and lets
    # the rest keep running. (Combined tp/sl always flattens everything.)
    flatten_mode: str = os.getenv("BOT_FLATTEN_MODE", "combined")
    # Multi-leg spec, e.g. "coinbase:btc_usd_spot:0.01,webull:spy_options:1".
    legs: str = os.getenv("BOT_LEGS", "")
    state_file: str = os.getenv("BOT_STATE_FILE", "bot_state.json")

    # ---- Runtime knobs -------------------------------------------------------
    dry_run: bool = _env_bool("BOT_DRY_RUN", True)
    webhook_host: str = os.getenv("BOT_WEBHOOK_HOST", "0.0.0.0")
    webhook_port: int = int(os.getenv("BOT_WEBHOOK_PORT", "8080"))
    webhook_secret: str = os.getenv("BOT_WEBHOOK_SECRET", "")
    poll_seconds: int = int(os.getenv("BOT_POLL_SECONDS", "60"))
    log_level: str = os.getenv("BOT_LOG_LEVEL", "INFO")

    # ---- Dashboard: the two windows the "Open trading windows" link pops open -
    window1_url: str = os.getenv("BOT_WINDOW_1_URL", "https://www.tradingview.com/chart/")
    window2_url: str = os.getenv("BOT_WINDOW_2_URL", "https://www.coinbase.com/advanced-trade/spot/BTC-USD")
    window1_title: str = os.getenv("BOT_WINDOW_1_TITLE", "Chart")
    window2_title: str = os.getenv("BOT_WINDOW_2_TITLE", "Broker")

    def free_mode(self) -> bool:
        """True when trading a free-form symbol/asset (the Alex path) rather
        than one of the fixed :class:`Instrument` products."""
        return bool(self.symbol_override) and self.asset_class is not None

    def symbol(self) -> str:
        if self.symbol_override:
            return self.symbol_override
        return INSTRUMENT_SYMBOLS[self.instrument]

    def multiplier(self) -> float:
        """Dollar-P/L weight for the current position (used by the P/L math)."""
        if self.free_mode():
            # Crypto/stock are 1:1; futures use a per-contract point value keyed
            # by the symbol's root (longest matching prefix wins, e.g. "MESU5" ->
            # "MES"). Unknown roots default to 1.0.
            if self.asset_class == AssetClass.FUTURES:
                sym = self.symbol().upper()
                for root in sorted(_FUTURES_MULTIPLIER, key=len, reverse=True):
                    if sym.startswith(root):
                        return _FUTURES_MULTIPLIER[root]
            return 1.0
        return INSTRUMENT_MULTIPLIER[self.instrument]

    def validate(self) -> None:
        if self.free_mode():
            allowed = ASSET_BROKERS.get(self.asset_class, ())
            if self.broker not in allowed:
                names = ", ".join(b.value for b in allowed) or "(none)"
                raise ValueError(
                    f"Asset class {self.asset_class.value!r} cannot be traded on "
                    f"broker {self.broker.value!r}. Allowed brokers: {names}."
                )
            if self.contract_size <= 0:
                raise ValueError("contract_size must be > 0")
            return
        allowed = INSTRUMENT_BROKERS[self.instrument]
        if self.broker not in allowed:
            names = ", ".join(b.value for b in allowed)
            raise ValueError(
                f"Instrument {self.instrument.value!r} cannot be traded on broker "
                f"{self.broker.value!r}. Allowed brokers: {names}."
            )
        if self.contract_size <= 0:
            raise ValueError("contract_size must be > 0")

    def describe(self) -> str:
        if self.free_mode():
            return (
                f"broker={self.broker.value} asset={self.asset_class.value} "
                f"symbol={self.symbol()} size={self.contract_size} "
                f"dry_run={self.dry_run}"
            )
        return (
            f"broker={self.broker.value} instrument={self.instrument.value} "
            f"symbol={self.symbol()} size={self.contract_size} "
            f"trend_tf={self.trend_timeframe} align={','.join(self.alignment_timeframes)} "
            f"dry_run={self.dry_run}"
        )
