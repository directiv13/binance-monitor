from decimal import Decimal
from typing import Any

import pytest

from binance_monitor.binance.models import Direction
from binance_monitor.monitor.direction import compute_direction, effective_price, size_usdt

D = Decimal


@pytest.mark.parametrize(
    ("side", "ps", "expected"),
    [
        ("BUY", "LONG", Direction.OPEN_LONG),
        ("SELL", "LONG", Direction.CLOSE_LONG),
        ("SELL", "SHORT", Direction.OPEN_SHORT),
        ("BUY", "SHORT", Direction.CLOSE_SHORT),
    ],
)
def test_hedge_mode_table(side: str, ps: str, expected: Direction) -> None:
    got = compute_direction(
        side=side,
        position_side=ps,
        reduce_only=False,
        close_position=False,
        qty=D(1),
        position_amount=D(0),
    )
    assert got is expected


def one_way(
    side: str, pos: str, qty: str = "1", *, reduce_only: bool = False, cp: bool = False
) -> Direction:
    return compute_direction(
        side=side,
        position_side="BOTH",
        reduce_only=reduce_only,
        close_position=cp,
        qty=D(qty),
        position_amount=D(pos),
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 1. reduceOnly / closePosition => close, whatever the stored position says
        ({"side": "SELL", "pos": "0", "reduce_only": True}, Direction.CLOSE_LONG),
        ({"side": "BUY", "pos": "0", "reduce_only": True}, Direction.CLOSE_SHORT),
        ({"side": "SELL", "pos": "5", "cp": True}, Direction.CLOSE_LONG),
        ({"side": "BUY", "pos": "-5", "cp": True, "qty": "0"}, Direction.CLOSE_SHORT),
        # 2a. no position
        ({"side": "BUY", "pos": "0"}, Direction.OPEN_LONG),
        ({"side": "SELL", "pos": "0"}, Direction.OPEN_SHORT),
        # 2b. same side as the position => increase
        ({"side": "BUY", "pos": "2"}, Direction.OPEN_LONG),
        ({"side": "SELL", "pos": "-2"}, Direction.OPEN_SHORT),
        # 2c. opposite side, qty <= |pos| => close (boundary included)
        ({"side": "SELL", "pos": "2", "qty": "1"}, Direction.CLOSE_LONG),
        ({"side": "SELL", "pos": "2", "qty": "2"}, Direction.CLOSE_LONG),
        ({"side": "BUY", "pos": "-2", "qty": "2"}, Direction.CLOSE_SHORT),
        # 2d. opposite side, qty > |pos| => flip
        ({"side": "SELL", "pos": "2", "qty": "3"}, Direction.FLIP_LONG_SHORT),
        ({"side": "BUY", "pos": "-2", "qty": "2.5"}, Direction.FLIP_SHORT_LONG),
    ],
)
def test_one_way_rules(kwargs: dict[str, Any], expected: Direction) -> None:
    assert one_way(**kwargs) is expected


def test_hedge_flag_is_fallback_only() -> None:
    # Missing/unknown positionSide but account is in hedge mode -> hedge logic
    got = compute_direction(
        side="SELL",
        position_side="",
        reduce_only=False,
        close_position=False,
        qty=D(1),
        position_amount=D(0),
        hedge_mode=True,
    )
    assert got is Direction.CLOSE_LONG


def test_labels() -> None:
    assert Direction.OPEN_LONG.label == "OPEN LONG"
    assert Direction.FLIP_LONG_SHORT.label == "FLIP LONG→SHORT"


def test_effective_price_priority() -> None:
    z = D(0)

    def ep(price: Decimal, avg: Decimal, last: Decimal, stop: Decimal, mark: Decimal) -> Decimal:
        return effective_price(
            price=price, avg_price=avg, last_fill_price=last, stop_price=stop, mark_price=mark
        )

    assert ep(D(5), D(4), D(3), D(2), D(1)) == 5
    assert ep(z, D(4), D(3), D(2), D(1)) == 4
    assert ep(z, z, D(3), D(2), D(1)) == 3
    assert ep(z, z, z, D(2), D(1)) == 2
    assert ep(z, z, z, z, D(1)) == 1
    assert ep(z, z, z, z, z) == 0


def test_size_usdt() -> None:
    assert size_usdt(D("64250"), D("0.015")) == D("963.75")
