"""The single pinned summary message: debounced create/edit/pin."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from binance_monitor.binance.models import SymbolInfo
from binance_monitor.db.repositories import Repositories
from binance_monitor.notify.summary import AccountView, render_summary
from binance_monitor.notify.telegram_publisher import MessageGoneError

log = logging.getLogger(__name__)

KV_KEY = "pinned_message_id"


class MessagePublisher(Protocol):
    async def send(self, text: str, *, disable_notification: bool = False) -> int: ...
    async def edit(self, message_id: int, text: str) -> None: ...
    async def pin(self, message_id: int) -> None: ...


class PinnedSummary:
    def __init__(
        self,
        repos: Repositories,
        publisher: MessagePublisher,
        symbol_info: Callable[[str], SymbolInfo],
        *,
        min_interval: float = 3.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repos = repos
        self._publisher = publisher
        self._info = symbol_info
        self._min_interval = min_interval
        self._now = now
        self._dirty = asyncio.Event()
        self._last_body: str | None = None

    def request_update(self) -> None:
        """Mark the summary stale; the background loop coalesces bursts into one edit."""
        self._dirty.set()

    async def run(self) -> None:
        self._dirty.set()  # initial render / (re)create on startup
        while True:
            await self._dirty.wait()
            await asyncio.sleep(self._min_interval)  # coalesce a burst of changes
            self._dirty.clear()
            try:
                await self.update()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pinned message update failed")

    async def _views(self) -> list[AccountView]:
        accounts = await self._repos.accounts.list_all()
        positions = await self._repos.positions.list_all_by_account()
        orders = await self._repos.orders.list_all_by_account()
        return [
            AccountView(a.name, a.status, positions.get(a.id, []), orders.get(a.id, []))
            for a in accounts
        ]

    async def update(self) -> bool:
        """Render and publish. Returns True if a message was sent or edited."""
        rendered = render_summary(await self._views(), self._info, self._now())
        raw_id = await self._repos.kv.get(KV_KEY)

        if raw_id is None:
            await self._create(rendered.text)
            self._last_body = rendered.body
            return True
        if rendered.body == self._last_body:
            return False  # nothing but the timestamp would change
        try:
            await self._publisher.edit(int(raw_id), rendered.text)
        except MessageGoneError:
            log.info("pinned message was deleted; creating a new one")
            await self._create(rendered.text)
        self._last_body = rendered.body
        return True

    async def _create(self, text: str) -> None:
        message_id = await self._publisher.send(text, disable_notification=True)
        await self._repos.kv.set(KV_KEY, str(message_id))
        await self._publisher.pin(message_id)
