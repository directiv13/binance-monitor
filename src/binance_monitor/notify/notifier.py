"""Concrete event sink: Telegram topic messages + Pushover + pinned refresh."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from binance_monitor.binance.models import SymbolInfo
from binance_monitor.monitor.notices import OrderNotice, PositionNotice
from binance_monitor.notify.formatting import (
    format_account_down,
    format_account_recovered,
    format_order,
    format_position,
    pushover_text,
)

log = logging.getLogger(__name__)


class TextPublisher(Protocol):
    async def send(self, text: str, *, disable_notification: bool = False) -> int: ...


class Broadcaster(Protocol):
    async def broadcast(self, title: str, message: str) -> None: ...


class Notifier:
    def __init__(
        self,
        publisher: TextPublisher,
        broadcaster: Broadcaster,
        symbol_info: Callable[[str], SymbolInfo],
        on_state_changed: Callable[[], None],
    ) -> None:
        self._publisher = publisher
        self._broadcaster = broadcaster
        self._info = symbol_info
        self._on_state_changed = on_state_changed

    async def _post(self, text: str) -> None:
        try:
            await self._publisher.send(text)
        except Exception:
            log.exception("failed to post to Telegram")

    async def order_event(self, notice: OrderNotice) -> None:
        await self._post(format_order(notice, self._info(notice.order.symbol)))

    async def position_event(self, notice: PositionNotice) -> None:
        info = self._info(notice.position.symbol)
        title, message = pushover_text(notice, info)

        async def push() -> None:
            try:
                await self._broadcaster.broadcast(title, message)
            except Exception:
                log.exception("Pushover broadcast failed")

        await asyncio.gather(self._post(format_position(notice, info)), push())

    async def account_down(self, account: str, error: str) -> None:
        await self._post(format_account_down(account, error))

    async def account_recovered(self, account: str) -> None:
        await self._post(format_account_recovered(account))

    def state_changed(self) -> None:
        self._on_state_changed()
