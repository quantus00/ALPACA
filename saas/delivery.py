"""Fan a signal out to every subscriber's Discord webhook / Telegram chat."""
from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import requests

from .settings import INSTRUMENT_LABELS

log = logging.getLogger(__name__)

# Only real Discord webhook URLs are accepted, so user input can't point the
# server at arbitrary hosts (SSRF).
DISCORD_RE = re.compile(r"^https://(discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")
TELEGRAM_CHAT_RE = re.compile(r"^-?\d{3,20}$")

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="deliver")


def format_signal(sig: dict, brand: str) -> str:
    label = INSTRUMENT_LABELS.get(sig["instrument"], sig["instrument"])
    if sig["kind"] == "trend":
        head = f"{label} — 4H TREND: {sig['direction']}"
    else:
        head = f"{label} — ENTRY SIGNAL: {sig['direction']}"
    price = f" @ {sig['price']:g}" if sig.get("price") else ""
    detail = f"\n{sig['detail']}" if sig.get("detail") else ""
    return f"[{brand}] {head}{price}{detail}\nNot financial advice."


def _send_discord(url: str, text: str) -> None:
    requests.post(url, json={"content": text}, timeout=10).raise_for_status()


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat_id, "text": text}, timeout=10).raise_for_status()


def deliver(sig: dict, users: list[sqlite3.Row], brand: str, telegram_token: str,
            wait: bool = False) -> int:
    """Queue a message per channel per user. Returns the number of sends queued."""
    text = format_signal(sig, brand)
    futures = []
    for u in users:
        if u["discord_webhook"] and DISCORD_RE.match(u["discord_webhook"]):
            futures.append(_pool.submit(_safe, _send_discord, u["discord_webhook"], text))
        if telegram_token and u["telegram_chat_id"] and TELEGRAM_CHAT_RE.match(u["telegram_chat_id"]):
            futures.append(_pool.submit(_safe, _send_telegram, telegram_token,
                                        u["telegram_chat_id"], text))
    if wait:
        for f in futures:
            f.result()
    return len(futures)


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("delivery via %s failed: %s", fn.__name__, exc)
