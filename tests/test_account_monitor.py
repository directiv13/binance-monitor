import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from binance_monitor.binance.client import BinanceAPIError
from binance_monitor.binance.models import Order, Position
from binance_monitor.binance.user_stream import Backoff
from binance_monitor.db.repositories import Repositories
from binance_monitor.monitor.account_monitor import AccountMonitor
from binance_monitor.monitor.lifecycle import OrderChangeKind as K

from .conftest import (
    RecordingSink,
    account_event,
    algo_event,
    order,
    order_event,
    pos,
    sample,
)


class FakeClient:
    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.algo: list[Order] | None = []
        self.positions_: list[Position] = []
        self.hedge = False
        self.fail_listen_key: list[Exception] = []
        self.listen_keys_closed = 0

    async def sync_time(self) -> int:
        return 0

    async def position_mode_hedge(self) -> bool:
        return self.hedge

    async def open_orders(self) -> list[Order]:
        return list(self.orders)

    async def open_algo_orders(self) -> list[Order] | None:
        return None if self.algo is None else list(self.algo)

    async def positions(self, symbol: str | None = None) -> list[Position]:
        return [p for p in self.positions_ if symbol is None or p.symbol == symbol]

    async def symbol_leverages(self) -> dict[str, int]:
        return {"BTCUSDT": 20}

    async def mark_price(self, symbol: str) -> Decimal:
        return Decimal("64300")

    async def create_listen_key(self) -> str:
        if self.fail_listen_key:
            raise self.fail_listen_key.pop(0)
        return "LISTENKEY"

    async def keepalive_listen_key(self) -> None:
        return None

    async def close_listen_key(self) -> None:
        self.listen_keys_closed += 1


def never_connector(url: str) -> AbstractAsyncContextManager[AsyncIterator[Any]]:
    raise AssertionError("stream should not be opened in this test")


async def make(
    repos: Repositories,
    client: FakeClient,
    sink: RecordingSink,
    *,
    name: str = "main",
    connector: Callable[[str], AbstractAsyncContextManager[AsyncIterator[Any]]] = never_connector,
    **kw: Any,
) -> AccountMonitor:
    account = await repos.accounts.get_by_name(name) or await repos.accounts.create(
        name, "k", "s", None
    )
    return AccountMonitor(
        account_id=account.id,
        name=name,
        client=client,
        repos=repos,
        sink=sink,
        stream_connector=connector,
        fstream_url="wss://fstream.test",
        **kw,
    )


# --------------------------------------------------------------------------- seeding / restart


async def test_first_snapshot_is_silent_and_restart_does_not_repost(repos: Repositories) -> None:
    client = FakeClient()
    client.orders = [order(1)]
    client.algo = [order(2, is_algo=True, type="STOP_MARKET", price="0", stop_price="60000")]
    client.positions_ = [pos()]

    sink = RecordingSink()
    monitor = await make(repos, client, sink)
    await monitor.reconcile()
    assert sink.orders == [] and sink.positions == []  # seeding: nothing announced
    assert (
        len(await repos.orders.list_for()) == 2
        and len(await repos.positions.list_all_by_account()) == 1
    )

    # "restart": a new monitor/sink on the same DB and same exchange state
    sink2 = RecordingSink()
    restarted = await make(repos, client, sink2)
    await restarted.reconcile()
    assert sink2.orders == [] and sink2.positions == []


async def test_reconcile_reports_missed_changes_labelled_resync(repos: Repositories) -> None:
    client = FakeClient()
    client.orders = [order(1), order(2), order(3)]
    client.positions_ = [pos("BTCUSDT", "0.015"), pos("ETHUSDT", "1")]
    sink = RecordingSink()
    monitor = await make(repos, client, sink)
    await monitor.reconcile()

    # while "disconnected": order 1 gone, order 2 re-priced, order 4 created,
    # BTC position closed, SOL position opened, ETH unchanged
    client.orders = [order(2, price="65000"), order(3), order(4, side="SELL", direction="")]
    client.positions_ = [pos("ETHUSDT", "1"), pos("SOLUSDT", "-5")]
    await monitor.reconcile()

    by_kind = {(n.kind, n.order.order_id): n for n in sink.orders}
    assert set(by_kind) == {(K.REMOVED, 1), (K.UPDATED, 2), (K.NEW, 4)}
    assert all(n.resync for n in sink.orders)
    assert by_kind[(K.NEW, 4)].order.direction == "OPEN_SHORT"  # computed for new orders
    assert by_kind[(K.UPDATED, 2)].order.direction == "OPEN_LONG"  # preserved
    assert {(p.opened, p.position.symbol) for p in sink.positions} == {
        (False, "BTCUSDT"),
        (True, "SOLUSDT"),
    }
    assert all(p.resync for p in sink.positions)
    assert {o.order_id for o in await repos.orders.list_for()} == {2, 3, 4}

    await monitor.reconcile()  # nothing changed -> nothing posted
    assert len(sink.orders) == 3 and len(sink.positions) == 2


async def test_algo_endpoint_unavailable_leaves_stored_algo_orders_alone(
    repos: Repositories,
) -> None:
    client = FakeClient()
    client.algo = [order(9, is_algo=True, type="STOP_MARKET", stop_price="1")]
    sink = RecordingSink()
    monitor = await make(repos, client, sink)
    await monitor.reconcile()
    client.algo = None  # endpoint 404
    await monitor.reconcile()
    assert sink.orders == [] and len(await repos.orders.list_for()) == 1


# --------------------------------------------------------------------------- order events


async def seeded(repos: Repositories, client: FakeClient, sink: RecordingSink) -> AccountMonitor:
    monitor = await make(repos, client, sink)
    await monitor.reconcile()  # empty seed
    return monitor


async def test_new_amend_partial_fill_filled_lifecycle(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    monitor = await seeded(repos, client, sink)
    oid = 8886774

    await monitor.handle_message(order_event())
    assert [(n.kind, n.order.direction) for n in sink.orders] == [(K.NEW, "OPEN_LONG")]
    assert sink.orders[0].ref_price == Decimal("64250.00")

    await monitor.handle_message(order_event())  # duplicate NEW (e.g. already seen): silent
    assert len(sink.orders) == 1

    await monitor.handle_message(order_event(x="AMENDMENT", p="64000"))
    assert sink.orders[-1].kind is K.UPDATED and sink.orders[-1].order.price == Decimal("64000")

    changes_before = sink.state_changes
    await monitor.handle_message(
        order_event(x="TRADE", X="PARTIALLY_FILLED", z="0.005", l="0.005", L="64000")
    )
    assert len(sink.orders) == 2  # partial fill posts NOTHING
    stored = await repos.orders.get(1, oid, False)
    assert (
        stored is not None
        and stored.filled_qty == Decimal("0.005")
        and stored.status == "PARTIALLY_FILLED"
    )
    assert sink.state_changes > changes_before  # ...but the pinned message is refreshed

    await monitor.handle_message(
        order_event(x="TRADE", X="FILLED", z="0.015", ap="64000", L="64000")
    )
    removed = sink.orders[-1]
    assert removed.kind is K.REMOVED and removed.reason == "filled"
    assert await repos.orders.get(1, oid, False) is None


async def test_direction_is_fixed_at_creation_and_reused(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    monitor = await seeded(repos, client, sink)
    await repos.positions.upsert(1, pos(amount="0.02"))  # long 0.02, one-way

    # SELL 0.03 > position => flip
    await monitor.handle_message(order_event(S="SELL", q="0.03", i=1))
    assert sink.orders[-1].order.direction == "FLIP_LONG_SHORT"
    # position later shrinks to 0 -> a recomputation would say OPEN_SHORT; label must stay
    await repos.positions.delete(1, "BTCUSDT", "BOTH")
    await monitor.handle_message(order_event(S="SELL", q="0.03", i=1, x="AMENDMENT", p="65000"))
    assert (
        sink.orders[-1].kind is K.UPDATED and sink.orders[-1].order.direction == "FLIP_LONG_SHORT"
    )
    await monitor.handle_message(order_event(S="SELL", q="0.03", i=1, x="CANCELED", X="CANCELED"))
    assert (
        sink.orders[-1].order.direction == "FLIP_LONG_SHORT"
        and sink.orders[-1].reason == "canceled"
    )


async def test_hedge_mode_direction_from_position_side(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.hedge = True
    monitor = await seeded(repos, client, sink)
    await monitor.handle_message(order_event(i=1, S="SELL", ps="LONG"))
    await monitor.handle_message(order_event(i=2, S="BUY", ps="SHORT"))
    assert [n.order.direction for n in sink.orders] == ["CLOSE_LONG", "CLOSE_SHORT"]


async def test_market_order_without_price_uses_fill_then_mark(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    monitor = await seeded(repos, client, sink)
    await monitor.handle_message(order_event(i=1, o="MARKET", p="0"))
    assert sink.orders[-1].ref_price == Decimal("64300")  # mark price fallback (REST)
    await monitor.handle_message(
        order_event(i=1, o="MARKET", p="0", x="TRADE", X="FILLED", ap="64111", z="0.015")
    )
    assert sink.orders[-1].ref_price == Decimal("64111")  # average fill price


async def test_algo_order_lifecycle(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    monitor = await seeded(repos, client, sink)
    await repos.positions.upsert(1, pos(amount="0.01", symbol="BNBUSDT"))
    await monitor.handle_message(algo_event())
    n = sink.orders[-1]
    assert n.kind is K.NEW and n.order.is_algo and n.order.direction == "CLOSE_LONG"
    assert n.order.stop_price == Decimal(750) and n.ref_price == Decimal(750)
    stored = await repos.orders.get(1, 2148719, True)
    assert stored is not None

    await monitor.handle_message(algo_event(X="TRIGGERING"))
    assert len(sink.orders) == 1  # silent
    await monitor.handle_message(algo_event(X="TRIGGERED", ai="123"))
    assert sink.orders[-1].kind is K.REMOVED and sink.orders[-1].reason == "triggered"
    await monitor.handle_message(algo_event(X="FINISHED"))
    assert len(sink.orders) == 2  # duplicate terminal event ignored
    assert await repos.orders.get(1, 2148719, True) is None


# --------------------------------------------------------------------------- position events


def acct(pa: str, ep: str = "64250", ps: str = "BOTH", up: str = "0.5") -> dict[str, Any]:
    return account_event({"pa": pa, "ep": ep, "ps": ps, "up": up})


async def test_position_open_resize_close_with_realized_pnl(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.positions_ = []
    monitor = await seeded(repos, client, sink)

    client.positions_ = [pos(mark="64310", leverage=10)]  # what REST enrichment will return
    await monitor.handle_message(acct("0.015"))
    assert [(p.opened, p.position.amount) for p in sink.positions] == [(True, Decimal("0.015"))]
    opened = sink.positions[0].position
    assert opened.mark_price == Decimal("64310") and opened.leverage == 10  # enriched
    assert (await repos.positions.get(1, "BTCUSDT", "BOTH")) is not None

    await monitor.handle_message(acct("0.03"))  # resize: stored, but no notification
    assert len(sink.positions) == 1
    stored = await repos.positions.get(1, "BTCUSDT", "BOTH")
    assert stored is not None and stored.amount == Decimal("0.03") and stored.leverage == 10

    # closing trade carries realized pnl, then ACCOUNT_UPDATE flattens the position
    await monitor.handle_message(
        order_event(i=5, S="SELL", x="TRADE", X="FILLED", rp="42.18", z="0.03", ap="65000")
    )
    await monitor.handle_message(acct("0"))
    assert [(p.opened) for p in sink.positions] == [True, False]
    assert sink.positions[1].realized_pnl == Decimal("42.18")
    assert sink.positions[1].position.amount == Decimal(
        "0.03"
    )  # closed position keeps its old size
    assert await repos.positions.get(1, "BTCUSDT", "BOTH") is None


async def test_one_way_sign_flip_is_closed_then_opened(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.positions_ = [pos(amount="0.015")]
    monitor = await seeded(repos, client, sink)
    client.positions_ = [pos(amount="-0.02")]
    await monitor.handle_message(acct("-0.02"))
    assert [p.opened for p in sink.positions] == [False, True]
    assert sink.positions[0].position.amount == Decimal("0.015")
    assert sink.positions[1].position.amount == Decimal("-0.02")


async def test_hedge_positions_are_tracked_per_side(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.hedge = True
    monitor = await seeded(repos, client, sink)
    client.positions_ = []
    await monitor.handle_message(
        account_event(
            {"pa": "1", "ps": "LONG", "ep": "100"}, {"pa": "-2", "ps": "SHORT", "ep": "100"}
        )
    )
    assert [(p.opened, p.position.side_label) for p in sink.positions] == [
        (True, "LONG"),
        (True, "SHORT"),
    ]
    await monitor.handle_message(account_event({"pa": "0", "ps": "SHORT"}))
    assert [(p.opened, p.position.side_label) for p in sink.positions][-1] == (False, "SHORT")
    assert await repos.positions.get(1, "BTCUSDT", "LONG") is not None


async def test_account_update_for_unchanged_size_posts_nothing(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.positions_ = [pos(amount="0.015")]
    monitor = await seeded(repos, client, sink)
    await monitor.handle_message(acct("0.015", up="9.9"))
    assert sink.positions == []
    stored = await repos.positions.get(1, "BTCUSDT", "BOTH")
    assert stored is not None and stored.unrealized_pnl == Decimal("9.9")


# --------------------------------------------------------------------------- supervisor / failures


async def fast_sleep(delay: float) -> None:
    """Backoff sleeps return immediately; periodic loops (keepalive, reconcile...) never fire."""
    await asyncio.sleep(0 if delay < 10 else 3600)


async def wait_for(predicate: Callable[[], bool], limit: float = 5.0) -> None:
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def test_run_loop_warns_once_recovers_once_and_processes_stream(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    client.fail_listen_key = [BinanceAPIError(-2015, "Invalid API-key"), ConnectionError("net")]
    urls: list[str] = []

    @asynccontextmanager
    async def connector(url: str) -> AsyncIterator[AsyncIterator[Any]]:
        urls.append(url)

        async def frames() -> AsyncIterator[str]:
            yield json.dumps(order_event())
            await asyncio.Event().wait()

        yield frames()

    monitor = await make(
        repos, client, sink, connector=connector, sleep=fast_sleep, backoff=Backoff(rng=lambda: 0.0)
    )
    task = asyncio.create_task(monitor.run())
    await wait_for(lambda: len(sink.orders) == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sink.down) == 1  # two failed attempts -> ONE warning
    assert "Invalid API-key" in sink.down[0][1]
    assert sink.recovered == ["main"]
    assert urls == ["wss://fstream.test/private/ws/LISTENKEY"]
    account = await repos.accounts.get_by_name("main")
    assert account is not None and account.status == "connected" and account.last_error is None
    assert client.listen_keys_closed == 1  # cleaned up on shutdown


async def test_listen_key_expired_reconnects_without_warning(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    sessions = 0

    @asynccontextmanager
    async def connector(url: str) -> AsyncIterator[AsyncIterator[Any]]:
        nonlocal sessions
        sessions += 1
        current = sessions

        async def frames() -> AsyncIterator[str]:
            if current == 1:
                yield json.dumps(sample("listen_key_expired"))
            await asyncio.Event().wait()

        yield frames()

    monitor = await make(repos, client, sink, connector=connector, sleep=fast_sleep)
    task = asyncio.create_task(monitor.run())
    await wait_for(lambda: sessions == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink.down == [] and sink.recovered == []


async def test_failure_and_recovery_notices_are_edge_triggered(repos: Repositories) -> None:
    client, sink = FakeClient(), RecordingSink()
    monitor = await make(repos, client, sink)
    for _ in range(3):
        await monitor._on_failure(ConnectionError("x"))
    assert len(sink.down) == 1
    account = await repos.accounts.get_by_name("main")
    assert account is not None and account.status == "error"
    await monitor._on_connected()
    await monitor._on_connected()
    assert sink.recovered == ["main"]
    await monitor._on_failure(ConnectionError("again"))  # a new outage warns again
    assert len(sink.down) == 2
