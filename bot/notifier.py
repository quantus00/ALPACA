"""Notification sink. Prints UPTREND / DOWNTREND (and entries) to the log and,
optionally, to a Discord/Slack webhook or Telegram bot if configured."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self.discord = os.getenv("NOTIFY_DISCORD_WEBHOOK", "")
        self.telegram_token = os.getenv("NOTIFY_TELEGRAM_TOKEN", "")
        self.telegram_chat = os.getenv("NOTIFY_TELEGRAM_CHAT", "")

    def _push(self, text: str) -> None:
        if self.discord:
            try:
                requests.post(self.discord, json={"content": text}, timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord notify failed: %s", exc)
        if self.telegram_token and self.telegram_chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    json={"chat_id": self.telegram_chat, "text": text},
                    timeout=10,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram notify failed: %s", exc)

    def notify_trend(self, trend: str, symbol: str, price: float) -> None:
        # This is the headline the user asked for: state UPTREND or DOWNTREND.
        text = f"[{symbol}] 4H TREND: {trend}  @ {price:g}"
        log.info(text)
        self._push(text)

    def notify_entry(self, action: str, symbol: str, price: float, size: float) -> None:
        text = f"[{symbol}] ENTRY {action.upper()} size={size:g} @ {price:g}"
        log.info(text)
        self._push(text)
