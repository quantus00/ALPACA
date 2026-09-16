# Connect the Alpaca server to Claude

Once `examples/smoke_test.py` prints "🎉 Everything works!", wire the server into
Claude so you can just *talk* to it ("what's my buying power?", "buy $10 of AAPL").

Everything below assumes you already installed the project on your droplet:

```bash
cd ~/ALPACA
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

Your Alpaca keys stay in your existing env file (e.g. `/etc/straddle-bot.env`,
which uses `ALPACA_API_KEY` / `ALPACA_API_SECRET`). The server understands those
names directly — nothing to rename. **Never paste the real secret into a file you
commit to git.**

---

## Option A — Claude Code (on the droplet)

This registers the server with the `claude` CLI, reading the key values straight
from your env file so you never type the secret:

```bash
source /etc/straddle-bot.env   # loads ALPACA_API_KEY / ALPACA_API_SECRET

claude mcp add alpaca \
  --env ALPACA_API_KEY="$ALPACA_API_KEY" \
  --env ALPACA_API_SECRET="$ALPACA_API_SECRET" \
  --env ALPACA_PAPER=true \
  -- "$HOME/ALPACA/.venv/bin/alpaca-mcp"
```

Check it connected:

```bash
claude mcp list
```

Then start `claude` and ask it something like *"Using the alpaca tools, is the
market open and what's my buying power?"*

---

## Option B — Claude Desktop (JSON config)

Edit Claude Desktop's config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add the `alpaca` entry (see `examples/claude_desktop_config.json` for the full
shape). Replace the two placeholders with your real paper keys, and use the full
path to the `alpaca-mcp` program from your venv:

```json
{
  "mcpServers": {
    "alpaca": {
      "command": "/absolute/path/to/ALPACA/.venv/bin/alpaca-mcp",
      "env": {
        "ALPACA_API_KEY": "PUT_YOUR_PAPER_KEY_HERE",
        "ALPACA_API_SECRET": "PUT_YOUR_PAPER_SECRET_HERE",
        "ALPACA_PAPER": "true"
      }
    }
  }
}
```

Fully quit and reopen Claude Desktop. The `alpaca` tools appear in the tools menu.

---

## Staying safe

- `ALPACA_PAPER=true` keeps everything on pretend money. Only switch to `false`
  (with live keys) when you truly mean to trade real money.
- Buying, selling, cancelling, and closing are marked so Claude asks before doing
  them. Looking things up (prices, account, positions) is always safe.
