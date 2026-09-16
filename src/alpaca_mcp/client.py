"""Async HTTP client for the Alpaca REST APIs.

A single shared ``httpx.AsyncClient`` is reused across tool calls. Errors are
translated into :class:`AlpacaError` with actionable messages so the agent can
recover (fix credentials, adjust parameters, retry later, ...).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .config import AlpacaConfig, load_config

_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class AlpacaError(RuntimeError):
    """Raised when an Alpaca API request fails. The message is agent-facing."""


class AlpacaClient:
    """Thin async wrapper over the Alpaca Trading and Market Data REST APIs."""

    def __init__(self, config: Optional[AlpacaConfig] = None) -> None:
        self.config = config or load_config()
        self._client: Optional[httpx.AsyncClient] = None

    # -- lifecycle -------------------------------------------------------
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
                headers={"accept": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- auth ------------------------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        if not self.config.has_credentials:
            raise AlpacaError(
                "Alpaca credentials are not configured. Set ALPACA_API_KEY_ID and "
                "ALPACA_API_SECRET_KEY in the server environment. Generate keys at "
                "https://app.alpaca.markets (use paper-trading keys unless "
                "ALPACA_PAPER=false)."
            )
        return {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.api_secret,
        }

    # -- request helpers -------------------------------------------------
    async def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        client = await self._get_client()
        url = f"{base_url}{path}"
        clean_params = _drop_none(params) if params else None
        clean_json = _drop_none(json) if json else None
        try:
            response = await client.request(
                method,
                url,
                params=clean_params,
                json=clean_json,
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as exc:  # network-level failure
            raise AlpacaError(
                f"Could not reach Alpaca ({method} {path}): {exc}. Check network "
                f"connectivity and that {base_url} is reachable."
            ) from exc

        if response.status_code >= 400:
            raise AlpacaError(_format_http_error(method, path, response, self.config))

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def trading_get(self, path: str, *, params: Optional[dict[str, Any]] = None) -> Any:
        return await self._request("GET", self.config.trading_base_url, path, params=params)

    async def trading_post(self, path: str, *, json: dict[str, Any]) -> Any:
        return await self._request("POST", self.config.trading_base_url, path, json=json)

    async def trading_patch(self, path: str, *, json: dict[str, Any]) -> Any:
        return await self._request("PATCH", self.config.trading_base_url, path, json=json)

    async def trading_delete(self, path: str, *, params: Optional[dict[str, Any]] = None) -> Any:
        return await self._request("DELETE", self.config.trading_base_url, path, params=params)

    async def data_get(self, path: str, *, params: Optional[dict[str, Any]] = None) -> Any:
        return await self._request("GET", self.config.data_base_url, path, params=params)


def _drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    """Remove keys whose value is None so they are omitted from the request."""
    return {k: v for k, v in mapping.items() if v is not None}


def _format_http_error(
    method: str, path: str, response: httpx.Response, config: AlpacaConfig
) -> str:
    detail = _extract_message(response)
    status = response.status_code
    base = f"Alpaca API {method} {path} failed ({status}): {detail}"

    hints = {
        401: (
            "Authentication failed. Verify ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY, "
            f"and that the keys match the current mode (mode={config.mode}; paper keys "
            "do not work against the live endpoint and vice versa)."
        ),
        403: (
            "Access forbidden. The account may lack permission for this action, or the "
            "keys may be for the wrong environment "
            f"(mode={config.mode}, trading_base_url={config.trading_base_url})."
        ),
        404: "Not found. Check the symbol, order id, or path is correct.",
        422: "Unprocessable request. Re-check parameter values against the Alpaca schema.",
        429: "Rate limited. Wait and retry with backoff; reduce request frequency.",
    }
    hint = hints.get(status)
    if hint is None and status >= 500:
        hint = "Alpaca server error. This is usually transient — retry with backoff."
    return f"{base}. {hint}" if hint else base


def _extract_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or "<no response body>"
    if isinstance(payload, dict):
        # Alpaca error bodies look like {"code": 40010001, "message": "..."}.
        message = payload.get("message") or payload.get("error")
        if message:
            code = payload.get("code")
            return f"{message} (code={code})" if code is not None else str(message)
    return str(payload)
