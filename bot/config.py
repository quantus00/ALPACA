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
    MGC_FUTURES = "mgc_futures"       # Tradovate Micro Gold


# Which brokers can trade which instrument. Used to validate the toggles.
INSTRUMENT_BROKERS: dict[Instrument, tuple[Broker, ...]] = {
    Instrument.BTC_USD_SPOT: (Broker.COINBASE,),
    Instrument.BTC_NANO_PERP: (Broker.COINBASE,),
    Instrument.SPY_OPTIONS: (Broker.ALPACA, Broker.WEBULL),
    Instrument.MES_FUTURES: (Broker.TRADOVATE,),
    Instrument.MGC_FUTURES: (Broker.TRADOVATE,),
}

# Default tradable symbol per instrument, per broker.
INSTRUMENT_SYMBOLS: dict[Instrument, str] = {
    Instrument.BTC_USD_SPOT: "BTC-USD",
    Instrument.BTC_NANO_PERP: "BTC-PERP-INTX",
    Instrument.SPY_OPTIONS: "SPY",
    Instrument.MES_FUTURES: "MES",
    Instrument.MGC_FUTURES: "MGC",
}

# Tick size (minimum price increment) per instrument. The ORB engine uses this
# for the breakout buffer, so gold (0.10) must not inherit the MES/ES 0.25.
INSTRUMENT_TICK_SIZES: dict[Instrument, float] = {
    Instrument.MES_FUTURES: 0.25,
    Instrument.MGC_FUTURES: 0.10,
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

    # ---- Prop-firm challenge guard (trailing-drawdown evaluation) ------------
    # When enabled, every entry is gated by bot.risk.ChallengeGuard so the bot
    # respects the profit target, daily loss cap, trailing drawdown, and the
    # flat-by-5pm / no-overnight session rules of a funded-account challenge.
    challenge_enabled: bool = _env_bool("BOT_CHALLENGE_ENABLED", False)
    challenge_start_balance: float = float(os.getenv("BOT_CHALLENGE_START", "50000"))
    challenge_profit_target: float = float(os.getenv("BOT_CHALLENGE_TARGET", "1500"))
    challenge_daily_loss: float = float(os.getenv("BOT_CHALLENGE_DAILY_LOSS", "500"))
    challenge_trailing_dd: float = float(os.getenv("BOT_CHALLENGE_TRAILING_DD", "1000"))
    # "intraday" (high-water mark on equity) or "eod" (trails on closing balance).
    challenge_trailing_mode: str = os.getenv("BOT_CHALLENGE_TRAILING_MODE", "intraday")
    challenge_stop_points: float = float(os.getenv("BOT_CHALLENGE_STOP_POINTS", "5"))

    # ---- Opening Range Breakout (ORB) strategy ------------------------------
    orb_minutes: int = int(os.getenv("BOT_ORB_MINUTES", "15"))
    orb_target_r: float = float(os.getenv("BOT_ORB_TARGET_R", "2"))
    orb_buffer_ticks: float = float(os.getenv("BOT_ORB_BUFFER_TICKS", "1"))
    orb_tick_size: float = float(os.getenv("BOT_ORB_TICK_SIZE", "0.25"))
    orb_max_trades: int = int(os.getenv("BOT_ORB_MAX_TRADES", "1"))
    orb_min_points: float = float(os.getenv("BOT_ORB_MIN_POINTS", "0"))
    orb_max_points: float = float(os.getenv("BOT_ORB_MAX_POINTS", "1e12"))

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

    def build_challenge_params(self):
        """Construct :class:`bot.risk.ChallengeParams` from these toggles."""
        from .risk import ChallengeParams

        return ChallengeParams(
            starting_balance=self.challenge_start_balance,
            profit_target=self.challenge_profit_target,
            daily_loss_limit=self.challenge_daily_loss,
            trailing_drawdown=self.challenge_trailing_dd,
            trailing_mode=self.challenge_trailing_mode,
        )

    def build_orb_params(self):
        """Construct :class:`bot.orb.ORBParams` from these toggles."""
        from .orb import ORBParams

        return ORBParams(
            or_minutes=self.orb_minutes,
            # Gold ticks at 0.10; index micros at 0.25. Instrument wins over the
            # BOT_ORB_TICK_SIZE default so MGC never inherits the MES increment.
            tick_size=INSTRUMENT_TICK_SIZES.get(self.instrument, self.orb_tick_size),
            entry_buffer_ticks=self.orb_buffer_ticks,
            stop="fixed" if self.challenge_stop_points > 0 else "range",
            stop_points=self.challenge_stop_points,
            target_r=self.orb_target_r,
            max_trades_per_day=self.orb_max_trades,
            min_or_points=self.orb_min_points,
            max_or_points=self.orb_max_points,
        )

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
        )
