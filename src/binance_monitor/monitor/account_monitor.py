"""One monitor per Binance account: snapshot, user data stream, events, reconciliation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from decimal import Decimal
from typing import Any, Protocol

from binance_monitor.binance.client import BinanceAPIError, BinanceRateLimitError
from binance_monitor.binance.events import parse_stream_message
from binance_monitor.binance.models import (
    ZERO,
    AccountUpdate,
    Direction,
    ListenKeyExpired,
    Order,
    OrderUpdate,
    Position,
)
from binance_monitor.binance.user_stream import (
    KEEPALIVE_SECONDS,
    MAX_CONNECTION_AGE_SECONDS,
    Backoff,
    StreamConnector,
    build_stream_url,
)
from binance_monitor.db.repositories import Repositories
from binance_monitor.monitor.diff import (
    PositionChange,
    PositionChangeKind,
    diff_orders,
    diff_positions,
)
from binance_monitor.monitor.direction import compute_direction, effective_price
from binance_monitor.monitor.lifecycle import OrderChangeKind, map_order_update
from binance_monitor.monitor.notices import EventSink, OrderNotice, PositionNotice

log = logging.getLogger(__name__)

_SIGNATURE_RE = re.compile(r"signature=[0-9a-fA-F]+")


class AccountClient(Protocol):
    """The subset of :class:`BinanceClient` the monitor needs (easy to fake in tests)."""

    async def sync_time(self) -> int: ...
    async def position_mode_hedge(self) -> bool: ...
    async def open_orders(self) -> list[Order]: ...
    async def open_algo_orders(self) -> list[Order] | None: ...
    async def positions(self, symbol: str | None = None) -> list[Position]: ...
    async def symbol_leverages(self) -> dict[str, int]: ...
    async def mark_price(self, symbol: str) -> Decimal: ...
    async def create_listen_key(self) -> str: ...
    async def keepalive_listen_key(self) -> None: ...
    async def close_listen_key(self) -> None: ...


class ReconnectRequested(Exception):
    """Planned reconnect (listenKey expired, 23h age): no warning, no backoff."""


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, BinanceAPIError):
        text = str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return _SIGNATURE_RE.sub("signature=***", text)[:300]


class AccountMonitor:
    def __init__(
        self,
        *,
        account_id: int,
        name: str,
        client: AccountClient,
        repos: Repositories,
        sink: EventSink,
        stream_connector: StreamConnector,
        fstream_url: str,
        reconcile_seconds: float = 300.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Backoff | None = None,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
        max_age_seconds: float = MAX_CONNECTION_AGE_SECONDS,
    ) -> None:
        self.account_id = account_id
        self.name = name
        self._client = client
        self._repos = repos
        self._sink = sink
        self._connect = stream_connector
        self._fstream_url = fstream_url
        self._reconcile_seconds = reconcile_seconds
        self._sleep = sleep
        self._backoff = backoff or Backoff()
        self._keepalive_seconds = keepalive_seconds
        self._max_age_seconds = max_age_seconds

        self._lock = asyncio.Lock()
        self._hedge = False
        self._leverage: dict[str, int] = {}
        self._realized: dict[tuple[str, str], Decimal] = {}
        self._reported_down = False
        self._listen_key: str | None = None

    # ================================================================== supervisor

    async def run(self) -> None:
        """Run forever (until cancelled): session -> failure -> backoff -> session ..."""
        try:
            while True:
                try:
                    await self._session()
                except asyncio.CancelledError:
                    raise
                except ReconnectRequested:
                    log.info("[%s] planned reconnect", self.name)
                    self._backoff.reset()
                except Exception as exc:
                    await self._on_failure(exc)
                    delay = self._backoff.next()
                    if isinstance(exc, BinanceRateLimitError):
                        delay = max(delay, exc.retry_after)
                    log.info("[%s] retrying in %.1fs", self.name, delay)
                    await self._sleep(delay)
        finally:
            await self._close_listen_key()

    async def _session(self) -> None:
        await self._client.sync_time()
        listen_key = await self._client.create_listen_key()
        self._listen_key = listen_key
        url = build_stream_url(self._fstream_url, listen_key)
        async with self._connect(url) as stream:
            # Frames received while we take the snapshot stay buffered in the socket, so no event
            # is lost between the snapshot and the first read. Handlers are idempotent.
            await self.reconcile()
            await self._on_connected()
            await self._run_until_first_done(
                self._read(stream),
                self._keepalive_loop(),
                self._reconcile_loop(),
                self._max_age_guard(),
            )

    @staticmethod
    async def _run_until_first_done(*coros: Coroutine[Any, Any, None]) -> None:
        tasks = [asyncio.ensure_future(c) for c in coros]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise the first failure
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read(self, stream: Any) -> None:
        async for raw in stream:
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning("[%s] non-JSON frame ignored", self.name)
                continue
            try:
                await self.handle_message(msg)
            except (ReconnectRequested, asyncio.CancelledError):
                raise
            except Exception:
                log.exception("[%s] failed to handle stream event", self.name)
        raise ConnectionError("user data stream closed by server")

    async def _keepalive_loop(self) -> None:
        while True:
            await self._sleep(self._keepalive_seconds)
            for attempt in range(3):
                try:
                    await self._client.keepalive_listen_key()
                    break
                except BinanceAPIError as exc:
                    if exc.code == -1125:  # listenKey does not exist
                        raise ReconnectRequested from exc
                    if attempt == 2:
                        raise
                except Exception:
                    if attempt == 2:
                        raise
                await self._sleep(20)

    async def _reconcile_loop(self) -> None:
        while True:
            await self._sleep(self._reconcile_seconds)
            await self.reconcile()

    async def _max_age_guard(self) -> None:
        await self._sleep(self._max_age_seconds)
        raise ReconnectRequested

    async def _close_listen_key(self) -> None:
        if self._listen_key is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.shield(self._client.close_listen_key())
        self._listen_key = None

    # ================================================================== status

    async def _on_failure(self, exc: BaseException) -> None:
        message = describe_error(exc)
        log.warning("[%s] connection problem: %s", self.name, message)
        with contextlib.suppress(Exception):
            await self._repos.accounts.set_status(self.account_id, "error", message)
        if not self._reported_down:
            self._reported_down = True
            await self._safe(self._sink.account_down(self.name, message))
        self._sink.state_changed()

    async def _on_connected(self) -> None:
        self._backoff.reset()
        await self._repos.accounts.set_status(self.account_id, "connected", None)
        if self._reported_down:
            self._reported_down = False
            await self._safe(self._sink.account_recovered(self.name))
        self._sink.state_changed()
        log.info("[%s] connected", self.name)

    async def _safe(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception:
            log.exception("[%s] notification failed", self.name)

    # ================================================================== stream events

    async def handle_message(self, msg: dict[str, Any]) -> None:
        event = parse_stream_message(msg)
        if event is None:
            return
        if isinstance(event, ListenKeyExpired):
            raise ReconnectRequested
        async with self._lock:
            if isinstance(event, OrderUpdate):
                await self._on_order_update(event)
            elif isinstance(event, AccountUpdate):
                await self._on_account_update(event)

    async def _on_order_update(self, u: OrderUpdate) -> None:
        repos = self._repos
        if not u.is_algo and u.realized_pnl != 0:
            key = (u.symbol, u.position_side)
            self._realized[key] = self._realized.get(key, ZERO) + u.realized_pnl

        stored = await repos.orders.get(self.account_id, u.order_id, u.is_algo)
        change = map_order_update(u, known=stored is not None)
        if change is None:
            return

        # The direction is fixed when the order is created and reused for later messages.
        if stored is not None and stored.direction:
            direction = stored.direction
        else:
            direction = str(
                await self._direction(
                    symbol=u.symbol,
                    side=u.side,
                    position_side=u.position_side,
                    reduce_only=u.reduce_only,
                    close_position=u.close_position,
                    qty=u.qty,
                )
            )
        order = Order(
            order_id=u.order_id,
            is_algo=u.is_algo,
            symbol=u.symbol,
            side=u.side,
            position_side=u.position_side,
            type=u.type,
            price=u.price,
            stop_price=u.stop_price
            if u.stop_price > 0
            else (stored.stop_price if stored else ZERO),
            qty=u.qty if u.qty > 0 else (stored.qty if stored else ZERO),
            filled_qty=u.filled_qty,
            status=u.status,
            reduce_only=u.reduce_only,
            direction=direction,
            update_time=u.event_time,
        )

        if change.kind is OrderChangeKind.REMOVED:
            if stored is not None:
                await repos.orders.delete(self.account_id, u.order_id, u.is_algo)
            ref = await self._ref_price(order, u.avg_price, u.last_fill_price)
            await self._sink.order_event(
                OrderNotice(OrderChangeKind.REMOVED, self.name, order, ref, change.reason)
            )
        elif change.kind is OrderChangeKind.NEW:
            await repos.orders.upsert(self.account_id, order)
            if stored is None:  # already known (e.g. seen by the snapshot): do not announce twice
                ref = await self._ref_price(order, u.avg_price, u.last_fill_price)
                await self._sink.order_event(
                    OrderNotice(OrderChangeKind.NEW, self.name, order, ref)
                )
        elif change.kind is OrderChangeKind.UPDATED:
            await repos.orders.upsert(self.account_id, order)
            ref = await self._ref_price(order, u.avg_price, u.last_fill_price)
            await self._sink.order_event(
                OrderNotice(OrderChangeKind.UPDATED, self.name, order, ref)
            )
        else:  # PROGRESS: partial fill -> DB + pinned only
            await repos.orders.upsert(self.account_id, order)
        self._sink.state_changed()

    async def _on_account_update(self, u: AccountUpdate) -> None:
        stored = {p.key: p for p in await self._repos.positions.list_for(self.account_id)}
        changes = diff_positions(stored, u.positions, full=False)
        for change in changes:
            await self._apply_position_change(change, resync=False, enrich=True)
        # Refresh entry price / PnL of positions that did not change size.
        changed_keys = {c.new.key for c in changes if c.new}
        for p in u.positions:
            if p.amount != 0 and p.key not in changed_keys and p.key in stored:
                await self._repos.positions.upsert(self.account_id, self._merge(p, stored[p.key]))
        self._sink.state_changed()

    # ================================================================== position helpers

    def _merge(self, new: Position, old: Position | None) -> Position:
        """Keep mark price / leverage that ACCOUNT_UPDATE does not carry."""
        leverage = new.leverage or (old.leverage if old else 0) or self._leverage.get(new.symbol, 0)
        mark = new.mark_price if new.mark_price > 0 else (old.mark_price if old else ZERO)
        return Position(
            symbol=new.symbol,
            position_side=new.position_side,
            amount=new.amount,
            entry_price=new.entry_price,
            mark_price=mark,
            unrealized_pnl=new.unrealized_pnl,
            leverage=leverage,
            update_time=new.update_time,
        )

    async def _apply_position_change(
        self, change: PositionChange, *, resync: bool, enrich: bool
    ) -> None:
        repos = self._repos
        key = (change.symbol, change.position_side)
        if change.kind is PositionChangeKind.CLOSED and change.old is not None:
            await repos.positions.delete(self.account_id, *key)
            realized = self._realized.pop(key, None)
            await self._sink.position_event(
                PositionNotice(False, self.name, change.old, realized, resync)
            )
        elif change.kind is PositionChangeKind.OPENED and change.new is not None:
            position = self._merge(change.new, None)
            if enrich:
                position = await self._enrich(position)
            await repos.positions.upsert(self.account_id, position)
            self._realized[key] = ZERO
            await self._sink.position_event(PositionNotice(True, self.name, position, None, resync))
        elif change.kind is PositionChangeKind.RESIZED and change.new is not None:
            await repos.positions.upsert(self.account_id, self._merge(change.new, change.old))

    async def _enrich(self, position: Position) -> Position:
        """Best effort: fetch mark price / leverage for a freshly opened position."""
        try:
            for fresh in await asyncio.wait_for(self._client.positions(position.symbol), 10):
                if fresh.key == position.key:
                    return Position(
                        symbol=position.symbol,
                        position_side=position.position_side,
                        amount=position.amount,
                        entry_price=position.entry_price or fresh.entry_price,
                        mark_price=fresh.mark_price,
                        unrealized_pnl=fresh.unrealized_pnl,
                        leverage=fresh.leverage or position.leverage,
                        update_time=position.update_time,
                    )
        except Exception as exc:
            log.debug("[%s] could not enrich position %s: %s", self.name, position.symbol, exc)
        return position

    # ================================================================== order helpers

    async def _direction(
        self,
        *,
        symbol: str,
        side: str,
        position_side: str,
        reduce_only: bool,
        close_position: bool,
        qty: Decimal,
    ) -> Direction:
        pos = await self._repos.positions.get(self.account_id, symbol, "BOTH")
        return compute_direction(
            side=side,
            position_side=position_side,
            reduce_only=reduce_only,
            close_position=close_position,
            qty=qty,
            position_amount=pos.amount if pos else ZERO,
            hedge_mode=self._hedge,
        )

    async def _ref_price(self, order: Order, avg: Decimal, last_fill: Decimal) -> Decimal:
        price = effective_price(
            price=order.price,
            avg_price=avg,
            last_fill_price=last_fill,
            stop_price=order.stop_price,
            mark_price=ZERO,
        )
        if price > 0:
            return price
        for p in await self._repos.positions.list_for(self.account_id):
            if p.symbol == order.symbol and p.mark_price > 0:
                return p.mark_price
        try:
            return await asyncio.wait_for(self._client.mark_price(order.symbol), 10)
        except Exception as exc:
            log.debug("[%s] mark price unavailable for %s: %s", self.name, order.symbol, exc)
            return ZERO

    # ================================================================== reconciliation

    async def reconcile(self) -> None:
        """Compare a fresh REST snapshot with the DB and report anything we missed."""
        async with self._lock:
            repos = self._repos
            client = self._client
            self._hedge = await client.position_mode_hedge()
            fresh_orders = await client.open_orders()
            fresh_algo = await client.open_algo_orders()
            fresh_positions = await client.positions()
            if any(p.leverage == 0 for p in fresh_positions):
                self._leverage.update(await client.symbol_leverages())
                fresh_positions = [
                    self._merge(p, None) if p.leverage == 0 else p for p in fresh_positions
                ]
            for p in fresh_positions:
                if p.leverage:
                    self._leverage[p.symbol] = p.leverage

            seed_key = f"seeded:{self.account_id}"
            silent = await repos.kv.get(seed_key) is None  # first ever snapshot: no announcements

            # ---- positions (first: order directions depend on the stored position)
            stored_positions = {p.key: p for p in await repos.positions.list_for(self.account_id)}
            changes = diff_positions(stored_positions, fresh_positions, full=True)
            for change in changes:
                if silent:
                    await self._apply_position_change_silent(change)
                else:
                    await self._apply_position_change(change, resync=True, enrich=False)
            changed_keys = {c.new.key for c in changes if c.new}
            for p in fresh_positions:
                if p.key not in changed_keys:  # metadata refresh (mark price, pnl, entry)
                    await repos.positions.upsert(self.account_id, p)

            # ---- orders
            stored_orders = await repos.orders.list_for(self.account_id)
            fresh_all = list(fresh_orders)
            if fresh_algo is None:  # endpoint unavailable: leave stored algo orders alone
                stored_orders = [o for o in stored_orders if not o.is_algo]
            else:
                fresh_all.extend(fresh_algo)
            diff = diff_orders(stored_orders, fresh_all)

            for o in diff.added:
                direction = await self._direction(
                    symbol=o.symbol,
                    side=o.side,
                    position_side=o.position_side,
                    reduce_only=o.reduce_only,
                    close_position=False,
                    qty=o.qty,
                )
                o = _with_direction(o, str(direction))
                await repos.orders.upsert(self.account_id, o)
                if not silent:
                    ref = await self._ref_price(o, ZERO, ZERO)
                    await self._sink.order_event(
                        OrderNotice(OrderChangeKind.NEW, self.name, o, ref, resync=True)
                    )
            for old, new in diff.changed:
                new = _with_direction(new, old.direction)
                await repos.orders.upsert(self.account_id, new)
                if not silent:
                    ref = await self._ref_price(new, ZERO, ZERO)
                    await self._sink.order_event(
                        OrderNotice(OrderChangeKind.UPDATED, self.name, new, ref, resync=True)
                    )
            stored_by_key = {(o.order_id, o.is_algo): o for o in stored_orders}
            for new in diff.progressed:
                old = stored_by_key[(new.order_id, new.is_algo)]
                await repos.orders.upsert(self.account_id, _with_direction(new, old.direction))
            for old in diff.removed:
                await repos.orders.delete(self.account_id, old.order_id, old.is_algo)
                if not silent:
                    ref = await self._ref_price(old, ZERO, ZERO)
                    await self._sink.order_event(
                        OrderNotice(
                            OrderChangeKind.REMOVED, self.name, old, ref, "no longer open", True
                        )
                    )

            if silent:
                await repos.kv.set(seed_key, "1")
            self._sink.state_changed()

    async def _apply_position_change_silent(self, change: PositionChange) -> None:
        if change.kind is PositionChangeKind.CLOSED:
            await self._repos.positions.delete(self.account_id, change.symbol, change.position_side)
        elif change.new is not None:
            await self._repos.positions.upsert(self.account_id, change.new)

    # ================================================================== periodic marks refresh

    async def refresh_marks(self) -> None:
        """Update mark price / unrealized PnL of stored positions (no notifications)."""
        try:
            fresh = await self._client.positions()
            if any(p.leverage == 0 for p in fresh):
                fresh = [self._merge(p, None) if p.leverage == 0 else p for p in fresh]
            await self._repos.positions.update_marks(self.account_id, fresh)
        except Exception as exc:
            log.debug("[%s] mark refresh failed: %s", self.name, exc)


def _with_direction(order: Order, direction: str) -> Order:
    return Order(
        order_id=order.order_id,
        is_algo=order.is_algo,
        symbol=order.symbol,
        side=order.side,
        position_side=order.position_side,
        type=order.type,
        price=order.price,
        stop_price=order.stop_price,
        qty=order.qty,
        filled_qty=order.filled_qty,
        status=order.status,
        reduce_only=order.reduce_only,
        direction=direction,
        update_time=order.update_time,
    )
