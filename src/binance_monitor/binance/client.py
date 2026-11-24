"""Thin in-house Binance USD-M Futures REST client (HMAC-SHA256 signing)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from binance_monitor.binance.events import (
    dec,
    parse_position_risk,
    parse_rest_algo_order,
    parse_rest_order,
    to_int,
)
from binance_monitor.binance.models import Order, Position, SymbolInfo

log = logging.getLogger(__name__)

RECV_WINDOW = 5000
TIMESTAMP_ERROR = -1021


class BinanceAPIError(Exception):
    def __init__(self, code: int, msg: str, status: int = 0) -> None:
        super().__init__(f"Binance error {code}: {msg}")
        self.code = code
        self.msg = msg
        self.status = status


class BinanceRateLimitError(BinanceAPIError):
    """HTTP 429 (rate limit) or 418 (IP ban)."""

    def __init__(self, status: int, retry_after: float) -> None:
        super().__init__(
            -1003, f"rate limited (HTTP {status}), retry after {retry_after:.0f}s", status
        )
        self.retry_after = retry_after


class BinanceClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        http: httpx.AsyncClient,
        *,
        recv_window: int = RECV_WINDOW,
        open_algo_orders_path: str = "/fapi/v1/openAlgoOrders",
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = http
        self._recv_window = recv_window
        self._open_algo_orders_path = open_algo_orders_path
        self._clock = clock
        self._monotonic = monotonic
        self.time_offset_ms = 0
        self._banned_until = 0.0

    # ------------------------------------------------------------------ core

    def _sign(self, query: str) -> str:
        return hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _timestamp(self) -> int:
        return int(self._clock() * 1000) + self.time_offset_ms

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        api_key: bool = False,
        _retry: bool = True,
    ) -> Any:
        now = self._monotonic()
        if now < self._banned_until:
            raise BinanceRateLimitError(429, self._banned_until - now)

        query_params: dict[str, Any] = dict(params or {})
        headers: dict[str, str] = {}
        if signed or api_key:
            headers["X-MBX-APIKEY"] = self._api_key
        if signed:
            query_params["recvWindow"] = self._recv_window
            query_params["timestamp"] = self._timestamp()
        query = urlencode(query_params)
        if signed:
            query = f"{query}&signature={self._sign(query)}"
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")

        response = await self._http.request(method, url, headers=headers, timeout=15.0)

        if response.status_code in (418, 429):
            retry_after = _retry_after(
                response, default=60.0 if response.status_code == 418 else 5.0
            )
            self._banned_until = self._monotonic() + retry_after
            raise BinanceRateLimitError(response.status_code, retry_after)

        try:
            data: Any = response.json()
        except ValueError:
            data = None

        if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0:
            code = int(data["code"])
            if code == TIMESTAMP_ERROR and signed and _retry:
                log.warning("Binance -1021 (timestamp outside recvWindow); resyncing clock")
                await self.sync_time()
                return await self._request(
                    method, path, params, signed=signed, api_key=api_key, _retry=False
                )
            raise BinanceAPIError(code, str(data.get("msg", "")), response.status_code)
        if response.status_code >= 400:
            raise BinanceAPIError(-1, response.text[:200], response.status_code)
        return data

    # ------------------------------------------------------------------ public

    async def sync_time(self) -> int:
        local_before = int(self._clock() * 1000)
        data = await self._request("GET", "/fapi/v1/time")
        server = to_int(data["serverTime"])
        self.time_offset_ms = server - local_before
        return self.time_offset_ms

    async def exchange_info(self) -> dict[str, SymbolInfo]:
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        result: dict[str, SymbolInfo] = {}
        for s in data.get("symbols", []):
            result[s["symbol"]] = SymbolInfo(
                symbol=s["symbol"],
                base_asset=str(s.get("baseAsset", "")),
                quote_asset=str(s.get("quoteAsset", "USDT")),
                price_precision=to_int(s.get("pricePrecision", 2)),
                quantity_precision=to_int(s.get("quantityPrecision", 3)),
            )
        return result

    async def mark_price(self, symbol: str) -> Decimal:
        data = await self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return dec(data.get("markPrice"))

    # ------------------------------------------------------------------ signed

    async def account(self) -> dict[str, Any]:
        """Signed call used to validate credentials."""
        data = await self._request("GET", "/fapi/v3/account", signed=True)
        return data if isinstance(data, dict) else {}

    async def position_mode_hedge(self) -> bool:
        data = await self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        return bool(data.get("dualSidePosition"))

    async def open_orders(self) -> list[Order]:
        data = await self._request("GET", "/fapi/v1/openOrders", signed=True)
        return [parse_rest_order(i) for i in data]

    async def open_algo_orders(self) -> list[Order] | None:
        """Open conditional (algo) orders; ``None`` if the endpoint is unavailable (404)."""
        try:
            data = await self._request("GET", self._open_algo_orders_path, signed=True)
        except BinanceAPIError as exc:
            if exc.status == 404:
                log.warning("algo open-orders endpoint %s not found", self._open_algo_orders_path)
                return None
            raise
        if isinstance(data, dict):
            data = data.get("orders") or data.get("data") or []
        return [parse_rest_algo_order(i) for i in data]

    async def positions(self, symbol: str | None = None) -> list[Position]:
        params = {"symbol": symbol} if symbol else None
        data = await self._request("GET", "/fapi/v3/positionRisk", params, signed=True)
        return parse_position_risk(data)

    async def symbol_leverages(self) -> dict[str, int]:
        """Best-effort leverage per symbol (positionRisk v3 may not include it)."""
        try:
            data = await self._request("GET", "/fapi/v1/symbolConfig", signed=True)
        except BinanceAPIError as exc:
            log.debug("symbolConfig unavailable: %s", exc)
            return {}
        return {str(i["symbol"]): to_int(i.get("leverage")) for i in data if "symbol" in i}

    # ------------------------------------------------------------------ listen key

    async def create_listen_key(self) -> str:
        data = await self._request("POST", "/fapi/v1/listenKey", api_key=True)
        return str(data["listenKey"])

    async def keepalive_listen_key(self) -> None:
        await self._request("PUT", "/fapi/v1/listenKey", api_key=True)

    async def close_listen_key(self) -> None:
        await self._request("DELETE", "/fapi/v1/listenKey", api_key=True)


def _retry_after(response: httpx.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(float(raw), 1.0)
        except ValueError:
            pass
    return default
