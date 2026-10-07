"""Environment-driven settings for the signal service (see deploy/.env.saas.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Instruments the worker analyses, keyed by the bot's Instrument value.
# MES structure is derived from SPY (see bot/data.py), so SPY/MES need Alpaca keys.
INSTRUMENT_LABELS: dict[str, str] = {
    "btc_usd_spot": "BTC/USD",
    "spy_options": "SPY",
    "mes_futures": "MES (S&P 500 micro)",
}


@dataclass
class Settings:
    brand: str = os.getenv("SAAS_BRAND_NAME", "Structure Signals")
    base_url: str = os.getenv("SAAS_BASE_URL", "http://localhost:8000").rstrip("/")
    secret_key: str = os.getenv("SAAS_SECRET_KEY", "")
    db_path: str = os.getenv("SAAS_DB_PATH", "data/saas.db")
    admin_emails: tuple[str, ...] = field(default_factory=lambda: tuple(
        e.strip().lower() for e in os.getenv("SAAS_ADMIN_EMAILS", "").split(",") if e.strip()
    ))
    secure_cookies: bool = _env_bool("SAAS_SECURE_COOKIES", True)

    # Pricing shown on the landing page; the real price lives in Stripe.
    price_label: str = os.getenv("SAAS_PRICE_LABEL", "$49 / month")
    trial_days: int = int(os.getenv("SAAS_TRIAL_DAYS", "7"))

    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_price_id: str = os.getenv("STRIPE_PRICE_ID", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Shared secret TradingView alerts must send (header or ?key=) to /ingest/tradingview.
    ingest_secret: str = os.getenv("SAAS_INGEST_SECRET", "")
    # Operator's Telegram bot; subscribers just give their chat id.
    telegram_bot_token: str = os.getenv("NOTIFY_TELEGRAM_TOKEN", "")
    telegram_bot_username: str = os.getenv("SAAS_TELEGRAM_BOT_USERNAME", "")

    instruments: tuple[str, ...] = field(default_factory=lambda: tuple(
        i.strip() for i in os.getenv("SAAS_INSTRUMENTS", "btc_usd_spot").split(",") if i.strip()
    ))
    poll_seconds: int = int(os.getenv("SAAS_POLL_SECONDS", "300"))

    def validate(self) -> None:
        if not self.secret_key or len(self.secret_key) < 32:
            raise ValueError("SAAS_SECRET_KEY must be set to a random string of 32+ chars")
        unknown = [i for i in self.instruments if i not in INSTRUMENT_LABELS]
        if unknown:
            raise ValueError(f"Unknown SAAS_INSTRUMENTS: {unknown}")

    @property
    def billing_enabled(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_id)
