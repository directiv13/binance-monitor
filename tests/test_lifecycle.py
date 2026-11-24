from decimal import Decimal
from typing import Any

import pytest

from binance_monitor.binance.events import parse_stream_message
from binance_monitor.binance.models import OrderUpdate
from binance_monitor.monitor.lifecycle import OrderChange, OrderChangeKind, map_order_update

from .conftest import algo_event, order_event

K = OrderChangeKind


def parse(msg: dict[str, Any]) -> OrderUpdate:
    ev = parse_stream_message(msg)
    assert isinstance(ev, OrderUpdate)
    return ev


def test_new_order() -> None:
    assert map_order_update(parse(order_event()), known=False) == OrderChange(K.NEW)


def test_amendment() -> None:
    ev = parse(order_event(x="AMENDMENT", X="NEW", p="64000"))
    assert map_order_update(ev, known=True) == OrderChange(K.UPDATED)
    assert ev.price == 64000


def test_partial_fill_produces_no_message() -> None:
    ev = parse(order_event(x="TRADE", X="PARTIALLY_FILLED", z="0.005", l="0.005", L="64250"))
    change = map_order_update(ev, known=True)
    assert change == OrderChange(K.PROGRESS)  # DB / pinned only, never a notification
    assert ev.filled_qty == Decimal("0.005")


@pytest.mark.parametrize(
    ("x", "status", "reason"),
    [
        ("TRADE", "FILLED", "filled"),
        ("CANCELED", "CANCELED", "canceled"),
        ("EXPIRED", "EXPIRED", "expired"),
        ("EXPIRED", "EXPIRED_IN_MATCH", "expired"),
        ("REJECTED", "REJECTED", "rejected"),
    ],
)
def test_removed(x: str, status: str, reason: str) -> None:
    ev = parse(order_event(x=x, X=status))
    assert map_order_update(ev, known=True) == OrderChange(K.REMOVED, reason)


def test_calculated_exec_type_ignored() -> None:
    ev = parse(order_event(x="CALCULATED", X="NEW"))
    assert map_order_update(ev, known=True) is None


def test_algo_new_and_fields() -> None:
    ev = parse(algo_event())
    assert ev.is_algo and ev.order_id == 2148719
    assert ev.stop_price == 750 and ev.reduce_only is True and ev.type == "STOP_MARKET"
    assert map_order_update(ev, known=False) == OrderChange(K.NEW)


def test_algo_triggering_is_silent() -> None:
    ev = parse(algo_event(X="TRIGGERING"))
    assert map_order_update(ev, known=True) == OrderChange(K.PROGRESS)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("CANCELED", "canceled"),
        ("EXPIRED", "expired"),
        ("REJECTED", "rejected"),
        ("TRIGGERED", "triggered"),
        ("FINISHED", "triggered"),
    ],
)
def test_algo_removed(status: str, reason: str) -> None:
    ev = parse(algo_event(X=status))
    assert map_order_update(ev, known=True) == OrderChange(K.REMOVED, reason)


def test_algo_duplicate_terminal_events_for_unknown_order_are_ignored() -> None:
    # TRIGGERED removed the row; the following FINISHED must not post a second removal
    assert map_order_update(parse(algo_event(X="FINISHED")), known=False) is None
    assert map_order_update(parse(algo_event(X="CANCELED")), known=False) is None
    # ...but a rejected conditional order is always reported
    assert map_order_update(parse(algo_event(X="REJECTED")), known=False) is not None


def test_parse_ignores_other_events_and_unwraps_combined_stream() -> None:
    assert parse_stream_message({"e": "TRADE_LITE", "s": "BTCUSDT"}) is None
    wrapped = {"stream": "x", "data": order_event()}
    assert isinstance(parse_stream_message(wrapped), OrderUpdate)
