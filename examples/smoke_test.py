"""A tiny 'does it work?' test for the Alpaca MCP server.

This script ONLY LOOKS — it checks your account, whether the market is open,
and one stock price. It does NOT buy or sell anything, so it is safe to run.

Run it (with paper-trading keys in your environment):

    python examples/smoke_test.py

It uses the paper (pretend-money) endpoint by default.
"""
from __future__ import annotations

import asyncio

from alpaca_mcp.client import AlpacaClient, AlpacaError


async def main() -> None:
    client = AlpacaClient()
    cfg = client.config
    print(f"Talking to Alpaca in '{cfg.mode}' mode ({cfg.trading_base_url}).\n")

    if not cfg.has_credentials:
        print(
            "No API keys found. Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY, "
            "then run this again."
        )
        return

    try:
        # 1) Your account
        account = await client.trading_get("/v2/account")
        print("✅ Account")
        print(f"   status:       {account.get('status')}")
        print(f"   cash:         ${account.get('cash')}")
        print(f"   buying power:  ${account.get('buying_power')}")
        print(f"   equity:       ${account.get('equity')}\n")

        # 2) Is the market open?
        clock = await client.trading_get("/v2/clock")
        state = "OPEN" if clock.get("is_open") else "CLOSED"
        print(f"✅ Market is {state}")
        print(f"   next open:  {clock.get('next_open')}")
        print(f"   next close: {clock.get('next_close')}\n")

        # 3) One stock price (latest trade for AAPL)
        trade = await client.data_get(
            "/v2/stocks/trades/latest", params={"symbols": "AAPL"}
        )
        aapl = (trade.get("trades") or {}).get("AAPL", {})
        print("✅ Latest AAPL trade")
        print(f"   price: ${aapl.get('p')}   size: {aapl.get('s')}   at: {aapl.get('t')}\n")

        print("🎉 Everything works! Your Alpaca MCP server can reach Alpaca.")
    except AlpacaError as exc:
        print(f"❌ Something went wrong:\n   {exc}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
