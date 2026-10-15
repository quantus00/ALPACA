# Alpaca MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes the
[Alpaca](https://alpaca.markets) **Trading** and **Market Data** REST APIs as
tools, so an LLM agent can inspect an account, manage orders and positions, and
pull stock/crypto market data.

Built with the Python MCP SDK (high-level `MCPServer`, formerly `FastMCP`).
Trading defaults to Alpaca's **paper** environment — you opt in to live trading
explicitly.

## Tools

| Group | Tools |
| --- | --- |
| **Account & status** | `alpaca_get_account`, `alpaca_get_clock`, `alpaca_get_calendar`, `alpaca_get_portfolio_history` |
| **Orders** | `alpaca_list_orders`, `alpaca_get_order`, `alpaca_create_order`, `alpaca_replace_order`, `alpaca_cancel_order`, `alpaca_cancel_all_orders` |
| **Positions** | `alpaca_list_positions`, `alpaca_get_position`, `alpaca_close_position`, `alpaca_close_all_positions` |
| **Assets** | `alpaca_list_assets`, `alpaca_get_asset` |
| **Market data (stocks)** | `alpaca_get_stock_bars`, `alpaca_get_stock_latest_quote`, `alpaca_get_stock_latest_trade`, `alpaca_get_stock_snapshot` |
| **Market data (crypto)** | `alpaca_get_crypto_bars`, `alpaca_get_crypto_latest_quote` |

Read-only tools are annotated `readOnlyHint`; order/position tools that change
account state are annotated as writes, and the liquidation/cancel tools carry
`destructiveHint` so clients can prompt before running them.

## Prerequisites

- Python 3.10+
- Alpaca API keys ([app.alpaca.markets](https://app.alpaca.markets)) — paper keys
  are free and recommended for testing.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configure

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
export ALPACA_API_KEY_ID=...
export ALPACA_API_SECRET_KEY=...
export ALPACA_PAPER=true   # keep true unless you mean to trade live
```

Recognized environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `ALPACA_API_KEY_ID` (or `APCA_API_KEY_ID`) | API key id | — |
| `ALPACA_API_SECRET_KEY` (or `APCA_API_SECRET_KEY`) | API secret | — |
| `ALPACA_PAPER` | `true` = paper endpoint, `false` = live | `true` |
| `ALPACA_TRADING_BASE_URL` | Override trading endpoint | derived from `ALPACA_PAPER` |
| `ALPACA_DATA_BASE_URL` | Override market-data endpoint | `https://data.alpaca.markets` |

The server starts and lists its tools even without credentials; a tool that needs
auth returns an actionable error until keys are set.

## Try it (safe, read-only)

A read-only smoke test that checks your account, the market clock, and one price
— it never buys or sells:

```bash
python examples/smoke_test.py
```

## Run

Over stdio (the default MCP transport):

```bash
alpaca-mcp
# or:  python -m alpaca_mcp.server
```

### Use with Claude Code

```bash
claude mcp add alpaca \
  --env ALPACA_API_KEY_ID=your-key-id \
  --env ALPACA_API_SECRET_KEY=your-secret \
  --env ALPACA_PAPER=true \
  -- alpaca-mcp
```

### Client config (`.mcp.json` style)

```json
{
  "mcpServers": {
    "alpaca": {
      "command": "alpaca-mcp",
      "env": {
        "ALPACA_API_KEY_ID": "your-key-id",
        "ALPACA_API_SECRET_KEY": "your-secret",
        "ALPACA_PAPER": "true"
      }
    }
  }
}
```

## Inspect / test

Use the MCP Inspector to exercise tools interactively:

```bash
npx @modelcontextprotocol/inspector alpaca-mcp
```

Or verify the server loads and enumerate its tools without any credentials:

```bash
python -c "import asyncio; from alpaca_mcp.server import mcp; \
print([t.name for t in asyncio.run(mcp.list_tools())])"
```

## Safety notes

- **Paper by default.** Live trading requires `ALPACA_PAPER=false` *and* live keys.
- `alpaca_create_order`, `alpaca_replace_order`, `alpaca_close_position`,
  `alpaca_close_all_positions`, `alpaca_cancel_order`, and
  `alpaca_cancel_all_orders` change real account state. The destructive ones are
  annotated so MCP clients can require confirmation.
- Credentials are read from the environment only; nothing is written to disk.

## Project layout

```
src/alpaca_mcp/
  __init__.py
  config.py    # env-driven configuration (paper/live, endpoints)
  client.py    # async httpx client, auth, actionable error mapping
  server.py    # MCPServer instance + all tool definitions
examples/
  smoke_test.py    # read-only 'does it work?' check
evaluation/
  alpaca_eval.xml  # sample evaluation questions (see file header)
```

## References

- [Alpaca API docs](https://docs.alpaca.markets)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
