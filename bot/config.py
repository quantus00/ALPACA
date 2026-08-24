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


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # ---- Primary toggles -----------------------------------------------------
    broker: Broker = Broker(os.getenv("BOT_BROKER", "coinbase"))
    instrument: Instrument = Instrument(os.getenv("BOT_INSTRUMENT", "btc_usd_spot"))

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

    # ---- Webull (official OpenAPI) ------------------------------------------
    # paper -> WEBULL_PAPER_APP_KEY/SECRET (fake $); live -> WEBULL_APP_KEY/SECRET (REAL $)
    webull_mode: str = os.getenv("WEBULL_MODE", "paper")

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

    def symbol(self) -> str:
        return INSTRUMENT_SYMBOLS[self.instrument]

    def validate(self) -> None:
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
        return (
            f"broker={self.broker.value} instrument={self.instrument.value} "
            f"symbol={self.symbol()} size={self.contract_size} "
            f"trend_tf={self.trend_timeframe} align={','.join(self.alignment_timeframes)} "
            f"dry_run={self.dry_run}"
            + (f" webull_mode={self.webull_mode}" if self.broker.value == "webull" else "")
        )
