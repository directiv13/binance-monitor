"""Pure diff logic for orders and positions (used by stream events and by reconciliation)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from binance_monitor.binance.models import ZERO, Order, Position

OrderKey = tuple[int, bool]
PositionKey = tuple[str, str]


@dataclass(slots=True)
class OrderDiff:
    added: list[Order] = field(default_factory=list)
    removed: list[Order] = field(default_factory=list)
    changed: list[tuple[Order, Order]] = field(default_factory=list)  # (old, new): price/qty
    progressed: list[Order] = field(default_factory=list)  # silent: filled qty / status only

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.progressed)


def diff_orders(stored: Iterable[Order], fresh: Iterable[Order]) -> OrderDiff:
    old = {(o.order_id, o.is_algo): o for o in stored}
    new = {(o.order_id, o.is_algo): o for o in fresh}
    diff = OrderDiff()
    for key, n in new.items():
        o = old.get(key)
        if o is None:
            diff.added.append(n)
        elif (o.price, o.stop_price, o.qty) != (n.price, n.stop_price, n.qty):
            diff.changed.append((o, n))
        elif (o.filled_qty, o.status) != (n.filled_qty, n.status):
            diff.progressed.append(n)
    diff.removed = [o for key, o in old.items() if key not in new]
    return diff


class PositionChangeKind(StrEnum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"
    RESIZED = "RESIZED"


@dataclass(frozen=True, slots=True)
class PositionChange:
    kind: PositionChangeKind
    symbol: str
    position_side: str
    old: Position | None  # state before (for CLOSED / RESIZED)
    new: Position | None  # state after (for OPENED / RESIZED)


def diff_positions(
    stored: Mapping[PositionKey, Position], fresh: Iterable[Position], *, full: bool
) -> list[PositionChange]:
    """Compare ``fresh`` positions with ``stored`` ones.

    ``full`` means ``fresh`` is a complete snapshot (REST): stored positions missing from it are
    treated as closed. For ACCOUNT_UPDATE (partial, only changed positions) pass ``full=False``.
    A sign flip (one-way mode) yields CLOSED followed by OPENED.
    """
    changes: list[PositionChange] = []
    seen: set[PositionKey] = set()
    for p in fresh:
        seen.add(p.key)
        old = stored.get(p.key)
        old_amt = old.amount if old else ZERO
        new_amt = p.amount
        if old_amt == new_amt:
            continue
        if old_amt == 0:
            changes.append(
                PositionChange(PositionChangeKind.OPENED, p.symbol, p.position_side, None, p)
            )
        elif new_amt == 0:
            changes.append(
                PositionChange(PositionChangeKind.CLOSED, p.symbol, p.position_side, old, None)
            )
        elif (old_amt > 0) != (new_amt > 0):
            changes.append(
                PositionChange(PositionChangeKind.CLOSED, p.symbol, p.position_side, old, None)
            )
            changes.append(
                PositionChange(PositionChangeKind.OPENED, p.symbol, p.position_side, None, p)
            )
        else:
            changes.append(
                PositionChange(PositionChangeKind.RESIZED, p.symbol, p.position_side, old, p)
            )
    if full:
        for key, old_pos in stored.items():
            if key not in seen and old_pos.amount != 0:
                changes.append(
                    PositionChange(PositionChangeKind.CLOSED, key[0], key[1], old_pos, None)
                )
    return changes
