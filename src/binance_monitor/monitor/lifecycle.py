"""Pure mapping: order stream event -> order lifecycle change."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from binance_monitor.binance.models import OrderUpdate


class OrderChangeKind(StrEnum):
    NEW = "NEW"
    UPDATED = "UPDATED"
    REMOVED = "REMOVED"
    PROGRESS = "PROGRESS"  # silent: partial fill or algo "triggering" -> DB / pinned only


@dataclass(frozen=True, slots=True)
class OrderChange:
    kind: OrderChangeKind
    reason: str | None = None  # for REMOVED: filled / canceled / expired / rejected / triggered


_REGULAR_TERMINAL = {
    "FILLED": "filled",
    "CANCELED": "canceled",
    "EXPIRED": "expired",
    "EXPIRED_IN_MATCH": "expired",
    "REJECTED": "rejected",
}

# ALGO_UPDATE statuses: NEW, TRIGGERING, TRIGGERED, FINISHED, CANCELED, REJECTED, EXPIRED.
# TRIGGERED / FINISHED mean the conditional order left the algo service (it became a regular
# order in the matching engine, which then produces its own ORDER_TRADE_UPDATE events).
_ALGO_TERMINAL = {
    "CANCELED": "canceled",
    "EXPIRED": "expired",
    "REJECTED": "rejected",
    "TRIGGERED": "triggered",
    "FINISHED": "triggered",
}


def map_order_update(update: OrderUpdate, *, known: bool) -> OrderChange | None:
    """Return the lifecycle change for ``update`` or ``None`` when it must be ignored.

    ``known`` tells whether the order is already in our DB (open-order state).
    """
    if update.is_algo:
        return _map_algo(update, known=known)
    return _map_regular(update)


def _map_regular(u: OrderUpdate) -> OrderChange | None:
    reason = _REGULAR_TERMINAL.get(u.status)
    if reason is not None:
        return OrderChange(OrderChangeKind.REMOVED, reason)
    if u.exec_type == "AMENDMENT":
        return OrderChange(OrderChangeKind.UPDATED)
    if u.exec_type == "NEW":
        return OrderChange(OrderChangeKind.NEW)
    if u.exec_type == "TRADE" and u.status == "PARTIALLY_FILLED":
        return OrderChange(OrderChangeKind.PROGRESS)
    return None


def _map_algo(u: OrderUpdate, *, known: bool) -> OrderChange | None:
    status = u.status
    if status == "NEW":
        return OrderChange(OrderChangeKind.NEW)
    if status == "TRIGGERING":
        return OrderChange(OrderChangeKind.PROGRESS) if known else None
    reason = _ALGO_TERMINAL.get(status)
    if reason is None:
        return None
    # FINISHED often follows TRIGGERED, CANCELED may repeat: only report removals of orders we
    # know about. A REJECTED conditional order is always worth reporting.
    if known or status == "REJECTED":
        return OrderChange(OrderChangeKind.REMOVED, reason)
    return None
