"""Cached exchangeInfo (symbol precisions), refreshed daily."""

from __future__ import annotations

import asyncio
import logging

from binance_monitor.binance.client import BinanceClient
from binance_monitor.binance.models import SymbolInfo

log = logging.getLogger(__name__)

REFRESH_SECONDS = 24 * 3600


class SymbolCache:
    def __init__(self, client: BinanceClient) -> None:
        self._client = client
        self._symbols: dict[str, SymbolInfo] = {}

    def get(self, symbol: str) -> SymbolInfo:
        info = self._symbols.get(symbol)
        if info is not None:
            return info
        # Unknown symbol (not loaded yet / delisted): precision -1 means "natural" formatting.
        base = symbol.removesuffix("USDT").removesuffix("USDC")
        quote = symbol.removeprefix(base) or "USDT"
        return SymbolInfo(symbol, base, quote, -1, -1)

    def set_all(self, symbols: dict[str, SymbolInfo]) -> None:
        self._symbols = symbols

    async def refresh(self) -> None:
        self._symbols = await self._client.exchange_info()
        log.info("loaded exchangeInfo for %d symbols", len(self._symbols))

    async def run(self) -> None:
        """Background task: initial load (with retries) then a refresh every day."""
        delay = 5.0
        while True:
            try:
                await self.refresh()
                delay = REFRESH_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("exchangeInfo refresh failed: %s", exc)
                delay = min(delay * 2, 300.0) if self._symbols == {} else 3600.0
            await asyncio.sleep(delay)
