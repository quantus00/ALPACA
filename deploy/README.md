# Droplet deployment

Run the bots unattended on a DigitalOcean droplet, and log Claude Code in there.

## Install (one time)

```bash
ssh root@YOUR_DROPLET_IP
git clone https://github.com/quantus00/ALPACA.git /opt/alpaca
cd /opt/alpaca
sudo bash deploy/setup_droplet.sh
```

The script installs Python + venv + deps, Node + the Claude Code CLI, creates a
`trader` service user, sets the clock to **America/New_York** (the 0DTE timer
assumes ET), writes `/etc/alpaca/alpaca.env`, and installs the systemd units.

Then fill in your keys:

```bash
sudo nano /etc/alpaca/alpaca.env     # root:trader, chmod 640 — never committed
```

## Logging Claude Code in on the droplet

Pick whichever fits how you use it:

| Way | Command | Good for |
|---|---|---|
| **Long-lived token** | `claude setup-token` on a machine with a browser, then put the value in `CLAUDE_CODE_OAUTH_TOKEN=` in `/etc/alpaca/alpaca.env` | unattended / systemd |
| **Interactive OAuth** | `sudo -u trader -i` then `claude` → `/login`; open the printed URL in your laptop browser and paste the code back | hands-on SSH use |
| **SSH port-forward** | `ssh -L 54545:localhost:54545 root@droplet` then `/login` — the browser redirect tunnels through | smoothest browser login |
| **API key** | `ANTHROPIC_API_KEY=` in the env file | Console/API billing |
| **Copy creds** | copy `~/.claude/.credentials.json` from a logged-in Linux box to `/home/trader/.claude/` (chmod 600) | quick clone |

Use **one** of these — don't mix a subscription login with an API key in the
same environment.

## Running the bots

```bash
# Trend bot + dashboard (long-running service on :8080)
sudo systemctl enable --now trend-bot.service
journalctl -u trend-bot -f

# SPY 0DTE bot — fires Mon–Fri at 10:25 ET; the bot waits until 10:34 ET entry
sudo systemctl enable --now spy-0dte.timer
systemctl list-timers spy-0dte.timer
journalctl -u spy-0dte -f
```

One-off orders still work by hand:

```bash
sudo -u trader -i
cd /opt/alpaca && source .venv/bin/activate
python cb_trade.py buy --product BTC-USD --usd 5          # paper
python spy_0dte_bot.py plan                                # preview strikes
```

## Safety

- Everything defaults to **paper / dry-run**. Going live is a deliberate edit to
  `/etc/alpaca/alpaca.env` (`ALPACA_BASE_URL`, `BOT_DRY_RUN`) plus explicit flags
  (`--live`) — there is no accidental path to real money.
- `spy-0dte.timer` uses `Persistent=false` so a droplet that was off does **not**
  fire a stale entry hours later.
- Keep `/etc/alpaca/alpaca.env` at `chmod 640` and out of git. If you expose the
  dashboard port publicly, put it behind a firewall or reverse proxy with auth.
- Trading bots lose money. Watch them on paper for a while before anything else.
