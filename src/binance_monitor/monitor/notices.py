"""Notification payloads emitted by the monitors and the sink interface that consumes them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from binance_monitor.binance.models import Order, Position
from binance_monitor.monitor.lifecycle import OrderChangeKind


@dataclass(frozen=True, slots=True)
class OrderNotice:
    kind: OrderChangeKind  # NEW, UPDATED or REMOVED
    account: str
    order: Order
    ref_price: Decimal  # price used for the USDT size (limit/avg/last fill/trigger/mark)
    reason: str | None = None
    resync: bool = False


@dataclass(frozen=True, slots=True)
class PositionNotice:
    opened: bool
    account: str
    position: Position
    realized_pnl: Decimal | None = None
    resync: bool = False


class EventSink(Protocol):
    async def order_event(self, notice: OrderNotice) -> None: ...

    async def position_event(self, notice: PositionNotice) -> None: ...

    async def account_down(self, account: str, error: str) -> None: ...

    async def account_recovered(self, account: str) -> None: ...

    def state_changed(self) -> None:
        """Order/position state changed: schedule a pinned message refresh."""
        ...
