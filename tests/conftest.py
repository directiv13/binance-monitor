from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from binance_monitor.binance.models import Order, Position
from binance_monitor.db.connection import Database, migrate
from binance_monitor.db.repositories import Repositories
from binance_monitor.monitor.notices import OrderNotice, PositionNotice

_SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "samples.json").read_text())


def sample(name: str) -> Any:
    return copy.deepcopy(_SAMPLES[name])


def order_event(**fields: Any) -> dict[str, Any]:
    msg = sample("order_trade_update")
    msg["o"].update(fields)
    return msg  # type: ignore[no-any-return]


def algo_event(**fields: Any) -> dict[str, Any]:
    msg = sample("algo_update")
    msg["ao"].update(fields)
    return msg  # type: ignore[no-any-return]


def account_event(*positions: dict[str, Any]) -> dict[str, Any]:
    msg = sample("account_update")
    base = msg["a"]["P"][0]
    msg["a"]["P"] = [{**base, **p} for p in positions]
    return msg  # type: ignore[no-any-return]


def pos(symbol: str = "BTCUSDT", amount: str = "0.015", side: str = "BOTH", **kw: Any) -> Position:
    return Position(
        symbol=symbol,
        position_side=side,
        amount=Decimal(amount),
        entry_price=Decimal(kw.get("entry", "64250")),
        mark_price=Decimal(kw.get("mark", "64300")),
        unrealized_pnl=Decimal(kw.get("pnl", "0.5")),
        leverage=kw.get("leverage", 10),
        update_time=1,
    )


def order(order_id: int = 1, **kw: Any) -> Order:
    return Order(
        order_id=order_id,
        is_algo=kw.get("is_algo", False),
        symbol=kw.get("symbol", "BTCUSDT"),
        side=kw.get("side", "BUY"),
        position_side=kw.get("position_side", "BOTH"),
        type=kw.get("type", "LIMIT"),
        price=Decimal(kw.get("price", "64250")),
        stop_price=Decimal(kw.get("stop_price", "0")),
        qty=Decimal(kw.get("qty", "0.015")),
        filled_qty=Decimal(kw.get("filled_qty", "0")),
        status=kw.get("status", "NEW"),
        reduce_only=kw.get("reduce_only", False),
        direction=kw.get("direction", "OPEN_LONG"),
        update_time=kw.get("update_time", 1),
    )


@pytest.fixture
async def repos(tmp_path: Path) -> AsyncIterator[Repositories]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    await migrate(db)
    yield Repositories(db)
    await db.close()


class RecordingSink:
    """EventSink that records everything instead of sending it."""

    def __init__(self) -> None:
        self.orders: list[OrderNotice] = []
        self.positions: list[PositionNotice] = []
        self.down: list[tuple[str, str]] = []
        self.recovered: list[str] = []
        self.state_changes = 0

    async def order_event(self, notice: OrderNotice) -> None:
        self.orders.append(notice)

    async def position_event(self, notice: PositionNotice) -> None:
        self.positions.append(notice)

    async def account_down(self, account: str, error: str) -> None:
        self.down.append((account, error))

    async def account_recovered(self, account: str) -> None:
        self.recovered.append(account)

    def state_changed(self) -> None:
        self.state_changes += 1
