from pathlib import Path

import pytest

from binance_monitor.db.connection import Database, migrate
from binance_monitor.db.repositories import DuplicateAccountError, Repositories

from .conftest import order, pos


async def test_migrations_are_idempotent_and_versioned(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    await db.connect()
    assert await migrate(db) == 1
    assert await migrate(db) == 1  # second run applies nothing
    async with db.conn.execute("SELECT COUNT(*) FROM schema_version") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1
    async with db.conn.execute("PRAGMA journal_mode") as cur:
        mode = await cur.fetchone()
    assert mode is not None and mode[0] == "wal"
    await db.close()


async def test_accounts_crud_and_unique_name(repos: Repositories) -> None:
    a = await repos.accounts.create("main", "kenc", "senc", 42)
    assert a.name == "main" and a.status == "connecting" and a.created_by == 42
    with pytest.raises(DuplicateAccountError):
        await repos.accounts.create("main", "x", "y", 1)
    await repos.accounts.set_status(a.id, "error", "boom")
    got = await repos.accounts.get_by_name("main")
    assert got is not None and got.status == "error" and got.last_error == "boom"
    assert [x.name for x in await repos.accounts.list_all()] == ["main"]
    assert await repos.accounts.delete(a.id) is True
    assert await repos.accounts.get(a.id) is None
    assert await repos.accounts.delete(a.id) is False


async def test_deleting_account_cascades_to_orders_positions(repos: Repositories) -> None:
    a = await repos.accounts.create("main", "k", "s", None)
    await repos.orders.upsert(a.id, order(1))
    await repos.positions.upsert(a.id, pos())
    await repos.kv.set(f"seeded:{a.id}", "1")
    await repos.accounts.delete(a.id)
    assert await repos.orders.list_for() == []
    assert await repos.positions.list_all_by_account() == {}
    assert await repos.kv.get(f"seeded:{a.id}") is None


async def test_subscribers_upsert_replace_unsubscribe(repos: Repositories) -> None:
    await repos.subscribers.upsert(1, "key-one")
    await repos.subscribers.upsert(1, "key-two")  # replaces
    await repos.subscribers.upsert(2, "other")
    active = await repos.subscribers.list_active()
    assert [(s.telegram_user_id, s.pushover_key) for s in active] == [(1, "key-two"), (2, "other")]
    await repos.subscribers.mark_inactive(2)
    assert [s.telegram_user_id for s in await repos.subscribers.list_active()] == [1]
    await repos.subscribers.upsert(2, "fresh")  # re-subscribing re-activates
    assert len(await repos.subscribers.list_active()) == 2
    assert await repos.subscribers.remove(1) is True
    assert await repos.subscribers.remove(1) is False  # fine if none existed


async def test_orders_roundtrip_keeps_decimals_and_pk_includes_is_algo(repos: Repositories) -> None:
    a = await repos.accounts.create("main", "k", "s", None)
    regular, algo = order(7, price="64250.10"), order(7, is_algo=True, stop_price="1.5")
    await repos.orders.upsert(a.id, regular)
    await repos.orders.upsert(a.id, algo)
    assert len(await repos.orders.list_for(a.id)) == 2
    assert await repos.orders.get(a.id, 7, False) == regular
    assert await repos.orders.get(a.id, 7, True) == algo
    await repos.orders.upsert(a.id, order(7, filled_qty="0.005", status="PARTIALLY_FILLED"))
    updated = await repos.orders.get(a.id, 7, False)
    assert updated is not None and str(updated.filled_qty) == "0.005"
    assert await repos.orders.delete(a.id, 7, False) is True
    assert await repos.orders.get(a.id, 7, False) is None
    assert await repos.orders.get(a.id, 7, True) is not None


async def test_positions_upsert_delete_and_update_marks(repos: Repositories) -> None:
    a = await repos.accounts.create("main", "k", "s", None)
    await repos.positions.upsert(a.id, pos(mark="100", leverage=5))
    await repos.positions.update_marks(a.id, [pos(mark="200", pnl="9", leverage=0), pos("ETHUSDT")])
    got = await repos.positions.get(a.id, "BTCUSDT", "BOTH")
    assert got is not None
    assert str(got.mark_price) == "200" and str(got.unrealized_pnl) == "9"
    assert got.leverage == 5  # leverage 0 in the refresh must not wipe it
    assert await repos.positions.get(a.id, "ETHUSDT", "BOTH") is None  # no row created
    assert await repos.positions.delete(a.id, "BTCUSDT", "BOTH") is True


async def test_kv(repos: Repositories) -> None:
    assert await repos.kv.get("pinned_message_id") is None
    await repos.kv.set("pinned_message_id", "10")
    await repos.kv.set("pinned_message_id", "11")
    assert await repos.kv.get("pinned_message_id") == "11"
    await repos.kv.delete("pinned_message_id")
    assert await repos.kv.get("pinned_message_id") is None
