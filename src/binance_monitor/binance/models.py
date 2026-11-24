"""Domain models shared by the Binance layer, the monitor and the notifiers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)


class Direction(StrEnum):
    OPEN_LONG = "OPEN_LONG"
    CLOSE_LONG = "CLOSE_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_SHORT = "CLOSE_SHORT"
    FLIP_LONG_SHORT = "FLIP_LONG_SHORT"
    FLIP_SHORT_LONG = "FLIP_SHORT_LONG"

    @property
    def label(self) -> str:
        return _DIRECTION_LABELS[self]


_DIRECTION_LABELS = {
    Direction.OPEN_LONG: "OPEN LONG",
    Direction.CLOSE_LONG: "CLOSE LONG",
    Direction.OPEN_SHORT: "OPEN SHORT",
    Direction.CLOSE_SHORT: "CLOSE SHORT",
    Direction.FLIP_LONG_SHORT: "FLIP LONG→SHORT",
    Direction.FLIP_SHORT_LONG: "FLIP SHORT→LONG",
}


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    name: str
    api_key_enc: str
    api_secret_enc: str
    status: str
    last_error: str | None
    created_at: str
    created_by: int | None


@dataclass(frozen=True, slots=True)
class Order:
    """An open order (regular or algo/conditional) as persisted in the DB."""

    order_id: int
    is_algo: bool
    symbol: str
    side: str
    position_side: str
    type: str
    price: Decimal
    stop_price: Decimal
    qty: Decimal
    filled_qty: Decimal
    status: str
    reduce_only: bool
    direction: str
    update_time: int


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    position_side: str  # BOTH / LONG / SHORT
    amount: Decimal  # signed
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    leverage: int
    update_time: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.position_side)

    @property
    def side_label(self) -> str:
        """LONG or SHORT for display."""
        if self.position_side in ("LONG", "SHORT"):
            return self.position_side
        return "LONG" if self.amount > 0 else "SHORT"

    @property
    def notional(self) -> Decimal:
        price = self.mark_price if self.mark_price > 0 else self.entry_price
        return abs(self.amount) * price


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    price_precision: int
    quantity_precision: int


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """Normalized ORDER_TRADE_UPDATE / ALGO_UPDATE event."""

    order_id: int
    is_algo: bool
    symbol: str
    side: str
    position_side: str
    type: str
    exec_type: str  # NEW, AMENDMENT, TRADE, CANCELED, ... (algo: the algo status)
    status: str  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, ... (algo: the algo status)
    price: Decimal
    stop_price: Decimal
    qty: Decimal
    filled_qty: Decimal
    avg_price: Decimal
    last_fill_price: Decimal
    reduce_only: bool
    close_position: bool
    realized_pnl: Decimal
    event_time: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    reason: str
    positions: tuple[Position, ...]
    event_time: int


@dataclass(frozen=True, slots=True)
class ListenKeyExpired:
    event_time: int
