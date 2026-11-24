from decimal import Decimal

from binance_monitor.binance.models import Position
from binance_monitor.monitor.diff import (
    PositionChange,
    diff_orders,
    diff_positions,
)
from binance_monitor.monitor.diff import PositionChangeKind as K

from .conftest import order, pos


def kinds(changes: list[PositionChange]) -> list[K]:
    return [c.kind for c in changes]


def stored(*positions: Position) -> dict[tuple[str, str], Position]:
    return {p.key: p for p in positions}


# ---------------------------------------------------------------- positions (one-way)


def test_open_one_way() -> None:
    assert kinds(diff_positions({}, [pos(amount="0.015")], full=False)) == [K.OPENED]


def test_close_one_way() -> None:
    changes = diff_positions(stored(pos()), [pos(amount="0")], full=False)
    assert kinds(changes) == [K.CLOSED]
    assert changes[0].old is not None and changes[0].new is None


def test_flip_one_way_is_close_then_open() -> None:
    changes = diff_positions(stored(pos(amount="0.015")), [pos(amount="-0.02")], full=False)
    assert kinds(changes) == [K.CLOSED, K.OPENED]
    assert changes[0].old is not None and changes[0].old.amount == Decimal("0.015")
    assert changes[1].new is not None and changes[1].new.amount == Decimal("-0.02")


def test_resize_is_not_open_or_close() -> None:
    up = diff_positions(stored(pos(amount="0.015")), [pos(amount="0.03")], full=False)
    down = diff_positions(stored(pos(amount="-0.03")), [pos(amount="-0.01")], full=False)
    assert kinds(up) == [K.RESIZED] and kinds(down) == [K.RESIZED]


def test_unchanged_amount_no_change() -> None:
    assert diff_positions(stored(pos()), [pos(mark="70000")], full=False) == []


def test_zero_for_unknown_position_ignored() -> None:
    assert diff_positions({}, [pos(amount="0")], full=False) == []


# ---------------------------------------------------------------- positions (hedge)


def test_hedge_long_and_short_are_independent() -> None:
    old = stored(pos(amount="1", side="LONG"))
    new = [pos(amount="1", side="LONG"), pos(amount="-2", side="SHORT")]
    changes = diff_positions(old, new, full=False)
    assert kinds(changes) == [K.OPENED]
    assert changes[0].position_side == "SHORT"


def test_hedge_close_one_leg() -> None:
    old = stored(pos(amount="1", side="LONG"), pos(amount="-2", side="SHORT"))
    changes = diff_positions(old, [pos(amount="0", side="SHORT")], full=False)
    assert [(c.kind, c.position_side) for c in changes] == [(K.CLOSED, "SHORT")]


# ---------------------------------------------------------------- positions (reconcile)


def test_full_snapshot_detects_missing_position_as_closed() -> None:
    changes = diff_positions(stored(pos("ETHUSDT", "1")), [pos("BTCUSDT", "0.5")], full=True)
    assert sorted((c.kind.value, c.symbol) for c in changes) == [
        ("CLOSED", "ETHUSDT"),
        ("OPENED", "BTCUSDT"),
    ]


def test_partial_update_does_not_close_missing_positions() -> None:
    changes = diff_positions(stored(pos("ETHUSDT", "1")), [pos("BTCUSDT", "0.5")], full=False)
    assert kinds(changes) == [K.OPENED]


# ---------------------------------------------------------------- orders (reconcile)


def test_order_diff() -> None:
    a, b, c, d = order(1), order(2), order(3), order(4)
    fresh = [
        a,  # unchanged
        order(2, price="65000"),  # price changed
        order(5),  # new
        order(4, filled_qty="0.005", status="PARTIALLY_FILLED"),  # progress only
    ]
    diff = diff_orders([a, b, c, d], fresh)
    assert [o.order_id for o in diff.added] == [5]
    assert [o.order_id for o in diff.removed] == [3]
    assert [(old.price, new.price) for old, new in diff.changed] == [
        (Decimal(64250), Decimal(65000))
    ]
    assert [o.order_id for o in diff.progressed] == [4]
    assert not diff.empty


def test_order_diff_distinguishes_algo_and_regular_ids() -> None:
    diff = diff_orders([order(7)], [order(7, is_algo=True)])
    assert len(diff.added) == 1 and len(diff.removed) == 1


def test_order_diff_empty() -> None:
    assert diff_orders([order(1)], [order(1)]).empty
