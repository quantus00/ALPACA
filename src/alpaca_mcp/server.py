"""Alpaca MCP server.

Exposes the Alpaca Trading and Market Data REST APIs as MCP tools. Tools are
named with an ``alpaca_`` prefix and grouped into:

  * Account & market status   (read-only)
  * Orders                     (read + write; trading defaults to paper)
  * Positions                  (read + write)
  * Assets                     (read-only reference data)
  * Market data                (read-only stock & crypto prices/bars)

Trading defaults to the **paper** endpoint. Set ``ALPACA_PAPER=false`` to place
real orders against a funded live account.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

try:
    # mcp >= 2.0 renamed the high-level server class.
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x fallback
    from mcp.server.fastmcp import FastMCP as _Server

from mcp.types import ToolAnnotations
from pydantic import Field

from .client import AlpacaClient
from .config import load_config

_config = load_config()
_client = AlpacaClient(_config)

mcp = _Server(
    "alpaca",
    instructions=(
        "Tools for the Alpaca brokerage: account info, order management, "
        "positions, tradable assets, and stock/crypto market data. Trading runs "
        f"against the {_config.mode} endpoint. Read-only tools are safe to call "
        "freely; order and position tools change account state — confirm intent "
        "before placing, replacing, cancelling, or liquidating."
    ),
)

# Reusable annotation presets ------------------------------------------------
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


def _symbols_param(symbols: list[str]) -> str:
    if not symbols:
        raise ValueError("Provide at least one symbol.")
    return ",".join(s.strip().upper() for s in symbols if s.strip())


def _crypto_symbols_param(symbols: list[str]) -> str:
    # Crypto symbols use a base/quote pair like "BTC/USD"; keep the slash.
    if not symbols:
        raise ValueError("Provide at least one crypto symbol, e.g. 'BTC/USD'.")
    return ",".join(s.strip().upper() for s in symbols if s.strip())


# ---------------------------------------------------------------------------
# Account & market status
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_account() -> dict[str, Any]:
    """Get the current account: balances, buying power, equity, and status."""
    return await _client.trading_get("/v2/account")


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_clock() -> dict[str, Any]:
    """Get the market clock: whether the market is open and next open/close times."""
    return await _client.trading_get("/v2/clock")


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_calendar(
    start: Annotated[
        Optional[str], Field(description="Start date, YYYY-MM-DD. Optional.")
    ] = None,
    end: Annotated[
        Optional[str], Field(description="End date, YYYY-MM-DD. Optional.")
    ] = None,
) -> list[dict[str, Any]]:
    """List market trading days with their open/close session times in a date range."""
    return await _client.trading_get(
        "/v2/calendar", params={"start": start, "end": end}
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_portfolio_history(
    period: Annotated[
        Optional[str],
        Field(description="Length of history, e.g. '1D', '1W', '1M', '1A'. Default 1M."),
    ] = None,
    timeframe: Annotated[
        Optional[str],
        Field(description="Resolution of each data point: '1Min', '5Min', '15Min', '1H', or '1D'."),
    ] = None,
    date_end: Annotated[
        Optional[str], Field(description="End date YYYY-MM-DD. Defaults to today.")
    ] = None,
    extended_hours: Annotated[
        Optional[bool],
        Field(description="Include extended-hours equity values."),
    ] = None,
) -> dict[str, Any]:
    """Get portfolio equity/profit-loss time series for the account."""
    return await _client.trading_get(
        "/v2/account/portfolio/history",
        params={
            "period": period,
            "timeframe": timeframe,
            "date_end": date_end,
            "extended_hours": extended_hours,
        },
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_list_orders(
    status: Annotated[
        Literal["open", "closed", "all"],
        Field(description="Which orders to return. Default 'open'."),
    ] = "open",
    limit: Annotated[
        int, Field(ge=1, le=500, description="Max orders to return (1-500). Default 50.")
    ] = 50,
    after: Annotated[
        Optional[str],
        Field(description="Only orders submitted after this RFC-3339 timestamp."),
    ] = None,
    until: Annotated[
        Optional[str],
        Field(description="Only orders submitted before this RFC-3339 timestamp."),
    ] = None,
    direction: Annotated[
        Literal["asc", "desc"],
        Field(description="Sort by submission time. Default 'desc'."),
    ] = "desc",
    symbols: Annotated[
        Optional[list[str]],
        Field(description="Restrict to these symbols, e.g. ['AAPL', 'TSLA']."),
    ] = None,
    nested: Annotated[
        bool,
        Field(description="Roll up multi-leg orders under a 'legs' field."),
    ] = True,
) -> list[dict[str, Any]]:
    """List orders filtered by status, time window, and symbol.

    Pagination: pass the submitted_at of the last returned order as `after`
    (with direction='asc') or `until` (with direction='desc') to page through.
    """
    return await _client.trading_get(
        "/v2/orders",
        params={
            "status": status,
            "limit": limit,
            "after": after,
            "until": until,
            "direction": direction,
            "symbols": _symbols_param(symbols) if symbols else None,
            "nested": nested,
        },
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_order(
    order_id: Annotated[str, Field(description="The order id (UUID) or client_order_id.")],
    by_client_order_id: Annotated[
        bool,
        Field(description="Treat order_id as a client_order_id instead of the Alpaca id."),
    ] = False,
) -> dict[str, Any]:
    """Get a single order by its id (or client order id)."""
    if by_client_order_id:
        return await _client.trading_get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": order_id},
        )
    return await _client.trading_get(f"/v2/orders/{order_id}")


@mcp.tool(annotations=_WRITE)
async def alpaca_create_order(
    symbol: Annotated[str, Field(description="Symbol to trade, e.g. 'AAPL' or 'BTC/USD'.")],
    side: Annotated[Literal["buy", "sell"], Field(description="Order side.")],
    type: Annotated[
        Literal["market", "limit", "stop", "stop_limit", "trailing_stop"],
        Field(description="Order type."),
    ] = "market",
    time_in_force: Annotated[
        Literal["day", "gtc", "opg", "cls", "ioc", "fok"],
        Field(description="Time in force. Default 'day'."),
    ] = "day",
    qty: Annotated[
        Optional[float],
        Field(gt=0, description="Number of shares/units. Provide qty OR notional, not both."),
    ] = None,
    notional: Annotated[
        Optional[float],
        Field(gt=0, description="Dollar amount for a fractional/notional order. Market-only."),
    ] = None,
    limit_price: Annotated[
        Optional[float],
        Field(gt=0, description="Required for 'limit' and 'stop_limit' orders."),
    ] = None,
    stop_price: Annotated[
        Optional[float],
        Field(gt=0, description="Required for 'stop' and 'stop_limit' orders."),
    ] = None,
    trail_price: Annotated[
        Optional[float],
        Field(gt=0, description="Trail amount in dollars for 'trailing_stop'."),
    ] = None,
    trail_percent: Annotated[
        Optional[float],
        Field(gt=0, description="Trail amount in percent for 'trailing_stop'."),
    ] = None,
    extended_hours: Annotated[
        bool,
        Field(description="Allow execution during extended hours (limit DAY orders only)."),
    ] = False,
    client_order_id: Annotated[
        Optional[str],
        Field(description="Caller-supplied id for idempotency/tracking (<=128 chars)."),
    ] = None,
) -> dict[str, Any]:
    """Submit a new order.

    Places a real order against the configured environment (paper by default,
    live if ALPACA_PAPER=false). Provide exactly one of `qty` or `notional`.
    """
    if (qty is None) == (notional is None):
        raise ValueError("Provide exactly one of `qty` or `notional`.")
    if type in ("limit", "stop_limit") and limit_price is None:
        raise ValueError(f"`limit_price` is required for a {type} order.")
    if type in ("stop", "stop_limit") and stop_price is None:
        raise ValueError(f"`stop_price` is required for a {type} order.")
    if type == "trailing_stop" and trail_price is None and trail_percent is None:
        raise ValueError("A trailing_stop order needs `trail_price` or `trail_percent`.")

    body = {
        "symbol": symbol.strip().upper(),
        "side": side,
        "type": type,
        "time_in_force": time_in_force,
        "qty": str(qty) if qty is not None else None,
        "notional": str(notional) if notional is not None else None,
        "limit_price": str(limit_price) if limit_price is not None else None,
        "stop_price": str(stop_price) if stop_price is not None else None,
        "trail_price": str(trail_price) if trail_price is not None else None,
        "trail_percent": str(trail_percent) if trail_percent is not None else None,
        "extended_hours": extended_hours,
        "client_order_id": client_order_id,
    }
    return await _client.trading_post("/v2/orders", json=body)


@mcp.tool(annotations=_WRITE)
async def alpaca_replace_order(
    order_id: Annotated[str, Field(description="Id of the open order to replace.")],
    qty: Annotated[Optional[float], Field(gt=0, description="New quantity.")] = None,
    limit_price: Annotated[Optional[float], Field(gt=0, description="New limit price.")] = None,
    stop_price: Annotated[Optional[float], Field(gt=0, description="New stop price.")] = None,
    trail: Annotated[
        Optional[float], Field(gt=0, description="New trail amount (price or percent).")
    ] = None,
    time_in_force: Annotated[
        Optional[Literal["day", "gtc", "opg", "cls", "ioc", "fok"]],
        Field(description="New time in force."),
    ] = None,
    client_order_id: Annotated[
        Optional[str], Field(description="New client order id for the replacement.")
    ] = None,
) -> dict[str, Any]:
    """Replace (amend) an existing open order. Only provided fields change."""
    body = {
        "qty": str(qty) if qty is not None else None,
        "limit_price": str(limit_price) if limit_price is not None else None,
        "stop_price": str(stop_price) if stop_price is not None else None,
        "trail": str(trail) if trail is not None else None,
        "time_in_force": time_in_force,
        "client_order_id": client_order_id,
    }
    return await _client.trading_patch(f"/v2/orders/{order_id}", json=body)


@mcp.tool(annotations=_DESTRUCTIVE)
async def alpaca_cancel_order(
    order_id: Annotated[str, Field(description="Id of the order to cancel.")],
) -> dict[str, Any]:
    """Cancel a single open order by id."""
    await _client.trading_delete(f"/v2/orders/{order_id}")
    return {"order_id": order_id, "status": "cancellation_requested"}


@mcp.tool(annotations=_DESTRUCTIVE)
async def alpaca_cancel_all_orders() -> list[dict[str, Any]]:
    """Cancel ALL open orders. Returns the per-order cancellation status."""
    result = await _client.trading_delete("/v2/orders")
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_list_positions() -> list[dict[str, Any]]:
    """List all open positions with market value and unrealized P/L."""
    return await _client.trading_get("/v2/positions")


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_position(
    symbol: Annotated[str, Field(description="Symbol of the open position, e.g. 'AAPL'.")],
) -> dict[str, Any]:
    """Get a single open position by symbol."""
    return await _client.trading_get(f"/v2/positions/{symbol.strip().upper()}")


@mcp.tool(annotations=_DESTRUCTIVE)
async def alpaca_close_position(
    symbol: Annotated[str, Field(description="Symbol of the position to close.")],
    qty: Annotated[
        Optional[float],
        Field(gt=0, description="Number of shares to liquidate. Omit to close fully."),
    ] = None,
    percentage: Annotated[
        Optional[float],
        Field(gt=0, le=100, description="Percent of the position to liquidate (0-100)."),
    ] = None,
) -> dict[str, Any]:
    """Liquidate an open position, fully or partially, by submitting a closing order."""
    if qty is not None and percentage is not None:
        raise ValueError("Provide at most one of `qty` or `percentage`.")
    return await _client.trading_delete(
        f"/v2/positions/{symbol.strip().upper()}",
        params={"qty": qty, "percentage": percentage},
    )


@mcp.tool(annotations=_DESTRUCTIVE)
async def alpaca_close_all_positions(
    cancel_orders: Annotated[
        bool,
        Field(description="Also cancel all open orders before liquidating."),
    ] = False,
) -> list[dict[str, Any]]:
    """Liquidate ALL open positions. Optionally cancel open orders first."""
    result = await _client.trading_delete(
        "/v2/positions", params={"cancel_orders": cancel_orders}
    )
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Assets (reference data)
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_list_assets(
    status: Annotated[
        Optional[Literal["active", "inactive"]],
        Field(description="Filter by asset status."),
    ] = "active",
    asset_class: Annotated[
        Optional[Literal["us_equity", "crypto"]],
        Field(description="Filter by asset class."),
    ] = None,
    exchange: Annotated[
        Optional[str],
        Field(description="Filter by exchange, e.g. 'NASDAQ', 'NYSE'."),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=1000, description="Max assets to return after filtering. Default 100.")
    ] = 100,
) -> list[dict[str, Any]]:
    """List tradable assets. The Alpaca endpoint is unpaginated, so `limit`
    truncates client-side to keep responses focused."""
    assets = await _client.trading_get(
        "/v2/assets",
        params={"status": status, "asset_class": asset_class, "exchange": exchange},
    )
    if isinstance(assets, list):
        return assets[:limit]
    return assets


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_asset(
    symbol_or_id: Annotated[
        str, Field(description="Symbol (e.g. 'AAPL') or asset UUID.")
    ],
) -> dict[str, Any]:
    """Get one asset's tradability, fractionability, and exchange details."""
    return await _client.trading_get(f"/v2/assets/{symbol_or_id.strip()}")


# ---------------------------------------------------------------------------
# Market data — stocks
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_stock_bars(
    symbols: Annotated[
        list[str], Field(description="Stock symbols, e.g. ['AAPL', 'MSFT'].")
    ],
    timeframe: Annotated[
        str,
        Field(description="Bar size: '1Min', '5Min', '15Min', '1Hour', '1Day', '1Week', '1Month'."),
    ] = "1Day",
    start: Annotated[
        Optional[str], Field(description="Start (RFC-3339 or YYYY-MM-DD). Optional.")
    ] = None,
    end: Annotated[
        Optional[str], Field(description="End (RFC-3339 or YYYY-MM-DD). Optional.")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=10000, description="Max bars per symbol (1-10000). Default 1000.")
    ] = 1000,
    adjustment: Annotated[
        Literal["raw", "split", "dividend", "all"],
        Field(description="Corporate-action adjustment. Default 'raw'."),
    ] = "raw",
    feed: Annotated[
        Optional[Literal["iex", "sip", "otc"]],
        Field(description="Data feed. 'iex' is free-tier; 'sip' needs a subscription."),
    ] = None,
    page_token: Annotated[
        Optional[str],
        Field(description="Pass the `next_page_token` from a prior call to page forward."),
    ] = None,
) -> dict[str, Any]:
    """Get historical OHLCV bars for one or more stocks.

    The response includes `next_page_token` when more data is available; pass it
    back as `page_token` to retrieve the next page.
    """
    return await _client.data_get(
        "/v2/stocks/bars",
        params={
            "symbols": _symbols_param(symbols),
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "limit": limit,
            "adjustment": adjustment,
            "feed": feed,
            "page_token": page_token,
        },
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_stock_latest_quote(
    symbols: Annotated[list[str], Field(description="Stock symbols, e.g. ['AAPL'].")],
    feed: Annotated[
        Optional[Literal["iex", "sip", "otc"]],
        Field(description="Data feed. Default 'iex' (free tier)."),
    ] = None,
) -> dict[str, Any]:
    """Get the latest bid/ask quote for one or more stocks."""
    return await _client.data_get(
        "/v2/stocks/quotes/latest",
        params={"symbols": _symbols_param(symbols), "feed": feed},
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_stock_latest_trade(
    symbols: Annotated[list[str], Field(description="Stock symbols, e.g. ['AAPL'].")],
    feed: Annotated[
        Optional[Literal["iex", "sip", "otc"]],
        Field(description="Data feed. Default 'iex' (free tier)."),
    ] = None,
) -> dict[str, Any]:
    """Get the latest executed trade (price and size) for one or more stocks."""
    return await _client.data_get(
        "/v2/stocks/trades/latest",
        params={"symbols": _symbols_param(symbols), "feed": feed},
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_stock_snapshot(
    symbols: Annotated[list[str], Field(description="Stock symbols, e.g. ['AAPL', 'TSLA'].")],
    feed: Annotated[
        Optional[Literal["iex", "sip", "otc"]],
        Field(description="Data feed. Default 'iex' (free tier)."),
    ] = None,
) -> dict[str, Any]:
    """Get a full snapshot per stock: latest trade, quote, minute bar, daily bar,
    and previous daily bar."""
    return await _client.data_get(
        "/v2/stocks/snapshots",
        params={"symbols": _symbols_param(symbols), "feed": feed},
    )


# ---------------------------------------------------------------------------
# Market data — crypto
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_crypto_bars(
    symbols: Annotated[
        list[str], Field(description="Crypto pairs, e.g. ['BTC/USD', 'ETH/USD'].")
    ],
    timeframe: Annotated[
        str,
        Field(description="Bar size: '1Min', '15Min', '1Hour', '1Day', etc."),
    ] = "1Day",
    start: Annotated[
        Optional[str], Field(description="Start (RFC-3339 or YYYY-MM-DD). Optional.")
    ] = None,
    end: Annotated[
        Optional[str], Field(description="End (RFC-3339 or YYYY-MM-DD). Optional.")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=10000, description="Max bars per symbol (1-10000). Default 1000.")
    ] = 1000,
    page_token: Annotated[
        Optional[str],
        Field(description="Pass the `next_page_token` from a prior call to page forward."),
    ] = None,
) -> dict[str, Any]:
    """Get historical OHLCV bars for one or more crypto pairs (US venue)."""
    return await _client.data_get(
        "/v1beta3/crypto/us/bars",
        params={
            "symbols": _crypto_symbols_param(symbols),
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "limit": limit,
            "page_token": page_token,
        },
    )


@mcp.tool(annotations=_READ_ONLY)
async def alpaca_get_crypto_latest_quote(
    symbols: Annotated[
        list[str], Field(description="Crypto pairs, e.g. ['BTC/USD'].")
    ],
) -> dict[str, Any]:
    """Get the latest bid/ask quote for one or more crypto pairs (US venue)."""
    return await _client.data_get(
        "/v1beta3/crypto/us/latest/quotes",
        params={"symbols": _crypto_symbols_param(symbols)},
    )


def main() -> None:
    """Console-script entry point. Runs the server over stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
