"""Repository layer on top of the shared SQLite connection."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite

from binance_monitor.binance.models import Account, Order, Position
from binance_monitor.db.connection import Database


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class DuplicateAccountError(Exception):
    pass


# --------------------------------------------------------------------------- accounts


def _account(row: aiosqlite.Row) -> Account:
    return Account(
        id=row["id"],
        name=row["name"],
        api_key_enc=row["api_key_enc"],
        api_secret_enc=row["api_secret_enc"],
        status=row["status"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


class AccountRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self, name: str, api_key_enc: str, api_secret_enc: str, created_by: int | None
    ) -> Account:
        try:
            async with self._db.transaction() as conn:
                cur = await conn.execute(
                    "INSERT INTO accounts (name, api_key_enc, api_secret_enc, status, created_at,"
                    " created_by) VALUES (?, ?, ?, 'connecting', ?, ?)",
                    (name, api_key_enc, api_secret_enc, utcnow_iso(), created_by),
                )
                account_id = cur.lastrowid
        except sqlite3.IntegrityError as exc:
            raise DuplicateAccountError(name) from exc
        account = await self.get(account_id) if account_id else None
        if account is None:  # pragma: no cover - cannot happen after a successful insert
            raise RuntimeError("account vanished after insert")
        return account

    async def get(self, account_id: int) -> Account | None:
        async with self._db.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)) as c:
            row = await c.fetchone()
        return _account(row) if row else None

    async def get_by_name(self, name: str) -> Account | None:
        async with self._db.conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)) as c:
            row = await c.fetchone()
        return _account(row) if row else None

    async def list_all(self) -> list[Account]:
        async with self._db.conn.execute("SELECT * FROM accounts ORDER BY id") as c:
            return [_account(r) async for r in c]

    async def set_status(self, account_id: int, status: str, last_error: str | None = None) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE accounts SET status = ?, last_error = ? WHERE id = ?",
                (status, last_error, account_id),
            )

    async def delete(self, account_id: int) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            await conn.execute("DELETE FROM kv WHERE key = ?", (f"seeded:{account_id}",))
            return cur.rowcount > 0


# --------------------------------------------------------------------------- subscribers


class Subscriber:
    __slots__ = ("active", "pushover_key", "telegram_user_id")

    def __init__(self, telegram_user_id: int, pushover_key: str, active: bool) -> None:
        self.telegram_user_id = telegram_user_id
        self.pushover_key = pushover_key
        self.active = active


class SubscriberRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(self, telegram_user_id: int, pushover_key: str) -> None:
        now = utcnow_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO subscribers (telegram_user_id, pushover_key, active, created_at,"
                " updated_at) VALUES (?, ?, 1, ?, ?) ON CONFLICT(telegram_user_id) DO UPDATE SET"
                " pushover_key = excluded.pushover_key, active = 1,"
                " updated_at = excluded.updated_at",
                (telegram_user_id, pushover_key, now, now),
            )

    async def remove(self, telegram_user_id: int) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM subscribers WHERE telegram_user_id = ?", (telegram_user_id,)
            )
            return cur.rowcount > 0

    async def get(self, telegram_user_id: int) -> Subscriber | None:
        async with self._db.conn.execute(
            "SELECT * FROM subscribers WHERE telegram_user_id = ?", (telegram_user_id,)
        ) as c:
            row = await c.fetchone()
        if row is None:
            return None
        return Subscriber(row["telegram_user_id"], row["pushover_key"], bool(row["active"]))

    async def list_active(self) -> list[Subscriber]:
        async with self._db.conn.execute(
            "SELECT * FROM subscribers WHERE active = 1 ORDER BY telegram_user_id"
        ) as c:
            return [Subscriber(r["telegram_user_id"], r["pushover_key"], True) async for r in c]

    async def mark_inactive(self, telegram_user_id: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE subscribers SET active = 0, updated_at = ? WHERE telegram_user_id = ?",
                (utcnow_iso(), telegram_user_id),
            )


# --------------------------------------------------------------------------- orders


def _order(row: aiosqlite.Row) -> Order:
    return Order(
        order_id=row["order_id"],
        is_algo=bool(row["is_algo"]),
        symbol=row["symbol"],
        side=row["side"],
        position_side=row["position_side"],
        type=row["type"],
        price=Decimal(row["price"]),
        stop_price=Decimal(row["stop_price"]),
        qty=Decimal(row["qty"]),
        filled_qty=Decimal(row["filled_qty"]),
        status=row["status"],
        reduce_only=bool(row["reduce_only"]),
        direction=row["direction"],
        update_time=row["update_time"],
    )


class OrderRepository:
    """Open orders only; rows are deleted when an order is removed."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, account_id: int, order_id: int, is_algo: bool) -> Order | None:
        async with self._db.conn.execute(
            "SELECT * FROM orders WHERE account_id = ? AND order_id = ? AND is_algo = ?",
            (account_id, order_id, int(is_algo)),
        ) as c:
            row = await c.fetchone()
        return _order(row) if row else None

    async def list_for(self, account_id: int | None = None) -> list[Order]:
        sql = "SELECT * FROM orders"
        args: tuple[int, ...] = ()
        if account_id is not None:
            sql += " WHERE account_id = ?"
            args = (account_id,)
        async with self._db.conn.execute(sql + " ORDER BY symbol, order_id", args) as c:
            return [_order(r) async for r in c]

    async def list_all_by_account(self) -> dict[int, list[Order]]:
        result: dict[int, list[Order]] = {}
        async with self._db.conn.execute("SELECT * FROM orders ORDER BY symbol, order_id") as c:
            async for r in c:
                result.setdefault(r["account_id"], []).append(_order(r))
        return result

    async def upsert(self, account_id: int, o: Order) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO orders (account_id, order_id, is_algo, symbol, side, position_side,"
                " type, price, stop_price, qty, filled_qty, status, reduce_only, direction,"
                " update_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account_id, order_id, is_algo) DO UPDATE SET symbol=excluded.symbol,"
                " side=excluded.side, position_side=excluded.position_side, type=excluded.type,"
                " price=excluded.price, stop_price=excluded.stop_price, qty=excluded.qty,"
                " filled_qty=excluded.filled_qty, status=excluded.status,"
                " reduce_only=excluded.reduce_only, direction=excluded.direction,"
                " update_time=excluded.update_time",
                (
                    account_id,
                    o.order_id,
                    int(o.is_algo),
                    o.symbol,
                    o.side,
                    o.position_side,
                    o.type,
                    str(o.price),
                    str(o.stop_price),
                    str(o.qty),
                    str(o.filled_qty),
                    o.status,
                    int(o.reduce_only),
                    o.direction,
                    o.update_time,
                ),
            )

    async def delete(self, account_id: int, order_id: int, is_algo: bool) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM orders WHERE account_id = ? AND order_id = ? AND is_algo = ?",
                (account_id, order_id, int(is_algo)),
            )
            return cur.rowcount > 0


# --------------------------------------------------------------------------- positions


def _position(row: aiosqlite.Row) -> Position:
    return Position(
        symbol=row["symbol"],
        position_side=row["position_side"],
        amount=Decimal(row["amount"]),
        entry_price=Decimal(row["entry_price"]),
        mark_price=Decimal(row["mark_price"]),
        unrealized_pnl=Decimal(row["unrealized_pnl"]),
        leverage=row["leverage"],
        update_time=row["update_time"],
    )


class PositionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_for(self, account_id: int) -> list[Position]:
        async with self._db.conn.execute(
            "SELECT * FROM positions WHERE account_id = ? ORDER BY symbol, position_side",
            (account_id,),
        ) as c:
            return [_position(r) async for r in c]

    async def list_all_by_account(self) -> dict[int, list[Position]]:
        result: dict[int, list[Position]] = {}
        async with self._db.conn.execute(
            "SELECT * FROM positions ORDER BY symbol, position_side"
        ) as c:
            async for r in c:
                result.setdefault(r["account_id"], []).append(_position(r))
        return result

    async def get(self, account_id: int, symbol: str, position_side: str) -> Position | None:
        async with self._db.conn.execute(
            "SELECT * FROM positions WHERE account_id = ? AND symbol = ? AND position_side = ?",
            (account_id, symbol, position_side),
        ) as c:
            row = await c.fetchone()
        return _position(row) if row else None

    async def upsert(self, account_id: int, p: Position) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO positions (account_id, symbol, position_side, amount, entry_price,"
                " mark_price, unrealized_pnl, leverage, update_time) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account_id, symbol, position_side) DO UPDATE SET"
                " amount=excluded.amount, entry_price=excluded.entry_price,"
                " mark_price=excluded.mark_price, unrealized_pnl=excluded.unrealized_pnl,"
                " leverage=excluded.leverage, update_time=excluded.update_time",
                (
                    account_id,
                    p.symbol,
                    p.position_side,
                    str(p.amount),
                    str(p.entry_price),
                    str(p.mark_price),
                    str(p.unrealized_pnl),
                    p.leverage,
                    p.update_time,
                ),
            )

    async def delete(self, account_id: int, symbol: str, position_side: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM positions WHERE account_id = ? AND symbol = ? AND position_side = ?",
                (account_id, symbol, position_side),
            )
            return cur.rowcount > 0

    async def update_marks(self, account_id: int, fresh: list[Position]) -> None:
        """Refresh mark price / PnL / leverage of rows that already exist (no open/close)."""
        async with self._db.transaction() as conn:
            for p in fresh:
                await conn.execute(
                    "UPDATE positions SET mark_price = ?, unrealized_pnl = ?,"
                    " leverage = CASE WHEN ? > 0 THEN ? ELSE leverage END"
                    " WHERE account_id = ? AND symbol = ? AND position_side = ?",
                    (
                        str(p.mark_price),
                        str(p.unrealized_pnl),
                        p.leverage,
                        p.leverage,
                        account_id,
                        p.symbol,
                        p.position_side,
                    ),
                )


# --------------------------------------------------------------------------- kv


class KvRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        async with self._db.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as c:
            row = await c.fetchone()
        return str(row["value"]) if row else None

    async def set(self, key: str, value: str) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    async def delete(self, key: str) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM kv WHERE key = ?", (key,))


class Repositories:
    """Bundle of all repositories sharing one Database."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.subscribers = SubscriberRepository(db)
        self.orders = OrderRepository(db)
        self.positions = PositionRepository(db)
        self.kv = KvRepository(db)
