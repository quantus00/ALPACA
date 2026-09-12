"""Runtime configuration for the Alpaca MCP server.

Credentials and endpoints come from environment variables. Missing credentials
are tolerated at construction time so the server can still start and list its
tools; the API client raises an actionable error only when a tool that needs
authentication is actually called.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Alpaca REST endpoints.
LIVE_TRADING_URL = "https://api.alpaca.markets"
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise ValueError(
        f"Environment variable {name}={raw!r} is not a valid boolean. "
        f"Use one of: {sorted(_TRUTHY | _FALSY)}."
    )


@dataclass(frozen=True)
class AlpacaConfig:
    """Resolved configuration for a server instance."""

    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    paper: bool

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key) and bool(self.api_secret)

    @property
    def mode(self) -> str:
        return "paper" if self.paper else "live"


def load_config() -> AlpacaConfig:
    """Build configuration from the environment.

    Recognized variables (first match wins for each credential):
      ALPACA_API_KEY_ID / APCA_API_KEY_ID / ALPACA_API_KEY          — API key id
      ALPACA_API_SECRET_KEY / APCA_API_SECRET_KEY / ALPACA_API_SECRET — API secret
      ALPACA_PAPER                              — "true" (default) uses the paper
                                                  trading endpoint; "false" uses live
      ALPACA_TRADING_BASE_URL                   — override the trading endpoint
      ALPACA_DATA_BASE_URL                      — override the market-data endpoint
    """
    api_key = (
        os.environ.get("ALPACA_API_KEY_ID")
        or os.environ.get("APCA_API_KEY_ID")
        or os.environ.get("ALPACA_API_KEY")  # matches the common straddle-bot env
        or ""
    ).strip()
    api_secret = (
        os.environ.get("ALPACA_API_SECRET_KEY")
        or os.environ.get("APCA_API_SECRET_KEY")
        or os.environ.get("ALPACA_API_SECRET")  # matches the common straddle-bot env
        or ""
    ).strip()

    paper = _env_bool("ALPACA_PAPER", default=True)
    default_trading = PAPER_TRADING_URL if paper else LIVE_TRADING_URL
    trading_base_url = os.environ.get("ALPACA_TRADING_BASE_URL", default_trading).rstrip("/")
    data_base_url = os.environ.get("ALPACA_DATA_BASE_URL", DATA_URL).rstrip("/")

    return AlpacaConfig(
        api_key=api_key,
        api_secret=api_secret,
        trading_base_url=trading_base_url,
        data_base_url=data_base_url,
        paper=paper,
    )
