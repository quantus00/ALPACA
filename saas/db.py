"""SQLite storage (stdlib only). One file, WAL mode, safe for web + worker."""
from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    created_at          INTEGER NOT NULL,
    is_admin            INTEGER NOT NULL DEFAULT 0,
    comped              INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id  TEXT,
    subscription_status TEXT NOT NULL DEFAULT 'none',
    current_period_end  INTEGER,
    discord_webhook     TEXT,
    telegram_chat_id    TEXT,
    instruments         TEXT NOT NULL DEFAULT '',
    api_token           TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_users_customer ON users(stripe_customer_id);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument  TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'trend' | 'entry'
    direction   TEXT NOT NULL,          -- UPTREND/DOWNTREND or BUY/SELL
    price       REAL NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,          -- 'engine' | 'tradingview'
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS stripe_events (
    id          TEXT PRIMARY KEY,
    received_at INTEGER NOT NULL
);
"""

ACTIVE_STATUSES = ("active", "trialing")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def has_access(user: sqlite3.Row | dict | None) -> bool:
    if not user:
        return False
    return bool(user["is_admin"] or user["comped"]
                or user["subscription_status"] in ACTIVE_STATUSES)


def insert_signal(conn: sqlite3.Connection, instrument: str, kind: str, direction: str,
                  price: float, detail: str, source: str) -> dict:
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO signals (instrument, kind, direction, price, detail, source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (instrument, kind, direction, price, detail, source, now))
    conn.commit()
    return {"id": cur.lastrowid, "instrument": instrument, "kind": kind,
            "direction": direction, "price": price, "detail": detail,
            "source": source, "created_at": now}


def recent_signals(conn: sqlite3.Connection, limit: int = 50,
                   instruments: list[str] | None = None) -> list[dict]:
    sql = "SELECT * FROM signals"
    args: list = []
    if instruments:
        sql += f" WHERE instrument IN ({','.join('?' * len(instruments))})"
        args += instruments
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def latest_trends(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT s.* FROM signals s JOIN (SELECT instrument, MAX(id) AS id FROM signals"
        " WHERE kind='trend' GROUP BY instrument) m ON s.id = m.id")
    return {r["instrument"]: dict(r) for r in rows}


def subscribers_for(conn: sqlite3.Connection, instrument: str) -> list[sqlite3.Row]:
    """Users with access who want this instrument (empty preference = all)."""
    rows = conn.execute(
        "SELECT * FROM users WHERE (discord_webhook IS NOT NULL AND discord_webhook != '')"
        " OR (telegram_chat_id IS NOT NULL AND telegram_chat_id != '')")
    out = []
    for r in rows:
        wanted = [i for i in (r["instruments"] or "").split(",") if i]
        if has_access(r) and (not wanted or instrument in wanted):
            out.append(r)
    return out
