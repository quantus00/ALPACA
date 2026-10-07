# Prompt for Claude Code on the server

Paste everything inside the block below into Claude Code running on your
droplet (e.g. over SSH from Termius). Before you start, have these ready: your
domain, a Stripe account, and optionally a Telegram bot token from @BotFather.

---

```text
You are on my Ubuntu server (DigitalOcean droplet). Deploy and finish the
"Structure Signals" subscription product from my repo, then verify it end to end.
Ask me for any secret or value you need; never invent one, never print secrets
back in full, and never commit .env files.

REPO
- git clone https://github.com/quantus00/ALPACA.git /opt/ALPACA (or git pull if it exists)
- Check out branch claude/shared-conversation-link-9c12ve (or main if it has been merged).
- Read saas/README.md, deploy/*, saas/*.py and tests/test_saas.py before changing anything.

1. SERVER PREP
- Install Docker Engine + compose plugin if missing (official apt repo), enable on boot.
- UFW: allow OpenSSH, 80, 443; deny everything else. Confirm SSH stays reachable BEFORE enabling.
- Make sure the box has swap (2 GB) if RAM < 2 GB.

2. CONFIG
- Copy deploy/.env.saas.example to /opt/ALPACA/.env.saas (chmod 600) and fill it in with me:
  SAAS_SECRET_KEY (generate), SAAS_BASE_URL=https://<my domain>, SAAS_ADMIN_EMAILS=<my email>,
  SAAS_INGEST_SECRET (generate), SAAS_INSTRUMENTS (ask me: btc_usd_spot only, or also
  spy_options/mes_futures which need ALPACA_API_KEY/SECRET), price label + trial days.
- Create deploy/.env with SAAS_DOMAIN=<my domain>.
- Ask me to point the domain's A record at this server's public IP; check it with `dig +short`
  before starting Caddy so Let's Encrypt succeeds.

3. STRIPE (test mode first)
- Walk me through: create a Product + recurring monthly Price -> STRIPE_PRICE_ID; secret key ->
  STRIPE_SECRET_KEY; Customer Portal enabled (Settings -> Billing -> Customer portal, allow cancel);
  webhook endpoint https://<domain>/stripe/webhook with events checkout.session.completed,
  customer.subscription.created, customer.subscription.updated, customer.subscription.deleted
  -> STRIPE_WEBHOOK_SECRET.

4. RUN
- cd /opt/ALPACA && docker compose -f deploy/docker-compose.yml up -d --build
- Check: docker compose ps (all healthy), curl https://<domain>/health, worker logs show a tick
  per instrument without errors.
- Run the test suite in a throwaway container:
  docker compose -f deploy/docker-compose.yml run --rm web sh -c "pip install -q pytest && python -m pytest -q tests"
  (copy tests/ into the image or mount it if needed).

5. END-TO-END CHECK (Stripe test mode)
- Sign up with my admin email; sign up a second test user; subscribe that user with card
  4242 4242 4242 4242; confirm the webhook flips them to trialing/active and /dashboard opens.
- Set a Discord webhook on that user, press "Send test alert", confirm delivery.
- POST a fake TradingView alert to /ingest/tradingview with the ingest secret; confirm it shows on
  the dashboard, arrives on Discord, and appears in GET /api/signals with the user's token.
- Cancel through "Manage billing"; confirm access is removed when Stripe sends the deletion event.

6. FINISH THE PRODUCT (implement, add tests, keep the existing code style)
- Password reset by email (signed, expiring token) using an SMTP provider I choose (Postmark,
  Resend or SES); ask me for credentials. Also send a welcome email on signup.
- Rate limiting on /login, /signup and the reset endpoint (e.g. per-IP sliding window in SQLite;
  no new infra).
- Nightly SQLite backup: a cron/systemd timer using `sqlite3 .backup` from the saas-data volume to
  /var/backups/saas with 14-day retention; tell me how to sync it off-box (DO Spaces / S3).
- Uptime: a systemd timer or cron that curls /health and alerts my Telegram if it fails.
- A public "track record" page listing past signals (delayed 24h for non-subscribers) to help sell.
- Run the full test suite after each change. Commit to the branch with clear messages and push;
  open a PR if one isn't open.

7. GO LIVE (only when I say so)
- Swap Stripe keys/price/webhook to live mode, restart, run one real $ checkout I can refund.
- Remind me to get the Terms page (saas/templates/terms.html) reviewed by a lawyer and to check
  whether my jurisdiction requires registration for selling trading signals.

Report back with: the URL, what you verified, anything that failed and why, and every value
I still need to supply.
```
