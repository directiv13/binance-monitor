"""MonitorManager: start / stop one AccountMonitor task per account at runtime."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx

from binance_monitor.binance.client import BinanceClient
from binance_monitor.binance.models import Account
from binance_monitor.binance.user_stream import StreamConnector
from binance_monitor.crypto import Crypto, DecryptionError
from binance_monitor.db.repositories import Repositories
from binance_monitor.monitor.account_monitor import AccountMonitor
from binance_monitor.monitor.notices import EventSink

log = logging.getLogger(__name__)


class MonitorManager:
    def __init__(
        self,
        *,
        repos: Repositories,
        crypto: Crypto,
        http: httpx.AsyncClient,
        sink: EventSink,
        stream_connector: StreamConnector,
        fapi_url: str,
        fstream_url: str,
        reconcile_seconds: float,
    ) -> None:
        self._repos = repos
        self._crypto = crypto
        self._http = http
        self._sink = sink
        self._connector = stream_connector
        self._fapi_url = fapi_url
        self._fstream_url = fstream_url
        self._reconcile_seconds = reconcile_seconds
        self._monitors: dict[int, AccountMonitor] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def make_client(self, api_key: str, api_secret: str) -> BinanceClient:
        return BinanceClient(self._fapi_url, api_key, api_secret, self._http)

    async def validate_credentials(self, api_key: str, api_secret: str) -> None:
        """Signed call to prove the credentials work. Raises BinanceAPIError on failure."""
        client = self.make_client(api_key, api_secret)
        await client.sync_time()
        await client.account()

    def is_running(self, account_id: int) -> bool:
        task = self._tasks.get(account_id)
        return task is not None and not task.done()

    async def start(self, account: Account) -> None:
        if self.is_running(account.id):
            return
        try:
            api_key = self._crypto.decrypt(account.api_key_enc)
            api_secret = self._crypto.decrypt(account.api_secret_enc)
        except DecryptionError as exc:
            log.error("[%s] %s", account.name, exc)
            await self._repos.accounts.set_status(account.id, "error", str(exc))
            return
        monitor = AccountMonitor(
            account_id=account.id,
            name=account.name,
            client=self.make_client(api_key, api_secret),
            repos=self._repos,
            sink=self._sink,
            stream_connector=self._connector,
            fstream_url=self._fstream_url,
            reconcile_seconds=self._reconcile_seconds,
        )
        self._monitors[account.id] = monitor
        self._tasks[account.id] = asyncio.create_task(monitor.run(), name=f"monitor:{account.name}")
        log.info("started monitor for account %s", account.name)

    async def stop(self, account_id: int) -> None:
        task = self._tasks.pop(account_id, None)
        self._monitors.pop(account_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        log.info("stopped monitor for account id=%s", account_id)

    async def start_all(self) -> None:
        for account in await self._repos.accounts.list_all():
            await self.start(account)

    async def stop_all(self) -> None:
        for account_id in list(self._tasks):
            await self.stop(account_id)

    async def refresh_marks_loop(self, interval: float) -> None:
        """Periodically refresh mark price / PnL so the pinned message stays current."""
        while True:
            await asyncio.sleep(interval)
            monitors = list(self._monitors.values())
            if monitors:
                await asyncio.gather(*(m.refresh_marks() for m in monitors))
            self._sink.state_changed()
