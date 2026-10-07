# Structure Signals — subscription signal service

The sellable product built on the trend engine in `bot/`. Customers sign up,
pay monthly through Stripe, and receive **4H trend flips** and **multi-timeframe
entry signals** on a dashboard, via Discord/Telegram, and over a REST API.

It sells *signals*, not execution: the service never stores customer broker
keys or places orders for them, which keeps it simple, safe and out of
money-manager territory.

| Piece | File | Job |
|-------|------|-----|
| Web app | `saas/app.py`, `saas/templates/` | Landing, sign-up/login, billing, dashboard, alert settings, API, admin |
| Billing | `saas/billing.py` | Stripe Checkout, Customer Portal, signed webhooks (REST, no SDK) |
| Worker | `saas/engine.py` | Runs `bot.strategy.StrategyRunner` per market, stores + fans out signals |
| Delivery | `saas/delivery.py` | Discord webhook / Telegram per subscriber |
| Storage | `saas/db.py` | SQLite (WAL) in a Docker volume |
| Deploy | `deploy/` | Dockerfile, docker-compose (web + worker + Caddy HTTPS) |

## Signal sources

1. **Built-in engine** (`worker` container) — polls every `SAAS_POLL_SECONDS`
   for each market in `SAAS_INSTRUMENTS`. BTC uses free Coinbase data; SPY/MES
   need Alpaca data keys.
2. **TradingView** — point a Pine alert at
   `https://DOMAIN/ingest/tradingview?instrument=mes_futures&key=SAAS_INGEST_SECRET`.
   Body is the Pine JSON (`{"action":"buy","price":...}`) or `UPTREND`/`DOWNTREND`.

## Access rules

A user has access when their Stripe status is `active`/`trialing`, or an admin
granted complimentary access, or they're an admin (`SAAS_ADMIN_EMAILS`).

## Run locally

```bash
pip install -r requirements-saas.txt
export SAAS_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))") SAAS_SECURE_COOKIES=false
gunicorn -b 127.0.0.1:8000 saas.wsgi:app      # web
python -m saas.engine                          # worker (separate shell)
pytest tests/
```

## Deploy

See `SERVER_BUILD_PROMPT.md` at the repo root — paste it into Claude Code on
your server and it will deploy, configure and verify everything.
