# Deploying the Coinbase 1-min FVG bot (DigitalOcean + GitHub)

The bot must run **24/7**, so it runs on a **DigitalOcean droplet** kept alive by
`systemd`. **GitHub** stores the code and auto-deploys on push. GitHub Actions is
**not** used to run the trading loop (Actions jobs cap at 6h and cron is throttled).

```
push to main ──▶ GitHub Action ──ssh──▶ droplet: git pull + restart systemd
                                             └── bot runs 24/7 here
```

## The bot

`bot/fvg_bot.py` — pulls 1-minute Coinbase candles, detects 3-bar Fair Value
Gaps, and routes enter/exit signals to the Coinbase broker.

- `btc_usd_spot` → long/flat only (spot can't short; bearish gaps are skipped).
- `btc_nano_perp` → long **and** short (needs an INTX perp-enabled account).
- Strategy knobs: `FVG_RR`, `FVG_BUFFER`, `FVG_MAX_HOLD`, `FVG_MIN_GAP_FRAC` (see `deploy/bot.env.example`).

Try it with no keys and no network first:

```bash
python -m bot.fvg_bot --selftest                 # synthetic logic check
python -m bot.fvg_bot --replay btc_1m.csv        # drive from a CSV, dry-run
python -m bot.fvg_bot                             # live 1m data, DRY-RUN (no orders)
```

## 1. One-time droplet setup

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
cd /opt
sudo git clone https://github.com/quantus00/ALPACA.git bot
sudo chown -R $USER:$USER /opt/bot
cd /opt/bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# secrets (root-only) — fill in real values
sudo cp deploy/bot.env.example /etc/bot.env
sudo nano /etc/bot.env          # keep BOT_DRY_RUN=true to start
sudo chmod 600 /etc/bot.env
```

## 2. Run it under systemd (24/7, auto-restart)

```bash
sudo cp deploy/fvgbot@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "fvgbot@$USER"

systemctl status "fvgbot@$USER"      # is it up?
journalctl -u "fvgbot@$USER" -f      # live logs — watch signals
```

## 3. Auto-deploy from GitHub

1. On the droplet, make a deploy key and authorize it:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/deploy -N ""
   cat ~/.ssh/deploy.pub >> ~/.ssh/authorized_keys
   cat ~/.ssh/deploy      # copy the PRIVATE key
   ```
2. In the repo: **Settings → Secrets and variables → Actions** add:
   - `DO_HOST` = droplet IP
   - `DO_USER` = your droplet username
   - `DO_SSH_KEY` = the private key printed above
3. Allow the deploy user to restart the service without a password prompt:
   ```bash
   echo "$USER ALL=(ALL) NOPASSWD: /bin/systemctl restart fvgbot@$USER, /bin/systemctl status fvgbot@$USER" | sudo tee /etc/sudoers.d/fvgbot
   ```
   (`.github/workflows/deploy.yml` runs `git pull` + `systemctl restart` on every push to `main`.)

## 4. Going live (real money) — do this deliberately

1. Run **paper (`BOT_DRY_RUN=true`) for days** and read the logs; confirm the
   1-min signals and simulated fills look right.
2. Put your **Coinbase live keys** in `/etc/bot.env` (never in git). Rotate any
   key that was ever exposed in a sheet or chat.
3. Flip `BOT_DRY_RUN=false` in `/etc/bot.env`, then
   `sudo systemctl restart "fvgbot@$USER"`. Start with the smallest
   `BOT_CONTRACT_SIZE`.

> ⚠️ No strategy is guaranteed. Trade only what you can afford to lose, and keep
> position size tiny until the live behaviour matches your backtests.
