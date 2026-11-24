"""Parsers: Binance user-data-stream events and REST payloads -> domain models."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from binance_monitor.binance.models import (
    ZERO,
    AccountUpdate,
    ListenKeyExpired,
    Order,
    OrderUpdate,
    Position,
)

StreamEvent = OrderUpdate | AccountUpdate | ListenKeyExpired


def dec(value: Any) -> Decimal:
    """Lenient Decimal conversion: None / '' / garbage -> 0."""
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ZERO


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


# --------------------------------------------------------------------------- stream events


def parse_stream_message(msg: dict[str, Any]) -> StreamEvent | None:
    """Parse one raw WS message. Returns None for events we do not care about."""
    if "data" in msg and "e" not in msg and isinstance(msg["data"], dict):
        msg = msg["data"]  # combined-stream wrapper
    event = msg.get("e")
    if event == "ORDER_TRADE_UPDATE":
        return _parse_order_trade_update(msg)
    if event == "ALGO_UPDATE":
        return _parse_algo_update(msg)
    if event == "ACCOUNT_UPDATE":
        return _parse_account_update(msg)
    if event == "listenKeyExpired":
        return ListenKeyExpired(event_time=to_int(msg.get("E")))
    return None


def _parse_order_trade_update(msg: dict[str, Any]) -> OrderUpdate | None:
    o = msg.get("o")
    if not isinstance(o, dict) or "i" not in o:
        return None
    return OrderUpdate(
        order_id=to_int(o["i"]),
        is_algo=False,
        symbol=str(o.get("s", "")),
        side=str(o.get("S", "")),
        position_side=str(o.get("ps", "BOTH")),
        type=str(o.get("o", "")),
        exec_type=str(o.get("x", "")),
        status=str(o.get("X", "")),
        price=dec(o.get("p")),
        stop_price=dec(o.get("sp")),
        qty=dec(o.get("q")),
        filled_qty=dec(o.get("z")),
        avg_price=dec(o.get("ap")),
        last_fill_price=dec(o.get("L")),
        reduce_only=to_bool(o.get("R")),
        close_position=to_bool(o.get("cp")),
        realized_pnl=dec(o.get("rp")),
        event_time=to_int(o.get("T") or msg.get("T") or msg.get("E")),
    )


def _parse_algo_update(msg: dict[str, Any]) -> OrderUpdate | None:
    o = msg.get("ao") or msg.get("o")
    if not isinstance(o, dict) or "aid" not in o:
        return None
    status = str(o.get("X", ""))
    return OrderUpdate(
        order_id=to_int(o["aid"]),
        is_algo=True,
        symbol=str(o.get("s", "")),
        side=str(o.get("S", "")),
        position_side=str(o.get("ps", "BOTH")),
        type=str(o.get("o", "")),
        exec_type=status,
        status=status,
        price=dec(o.get("p")),
        stop_price=dec(o.get("tp")),
        qty=dec(o.get("q")),
        filled_qty=dec(o.get("aq")),
        avg_price=dec(o.get("ap")),
        last_fill_price=ZERO,
        reduce_only=to_bool(o.get("R")),
        close_position=to_bool(o.get("cp")),
        realized_pnl=ZERO,
        event_time=to_int(msg.get("T") or msg.get("E")),
        reason=str(o.get("rm", "") or ""),
    )


def _parse_account_update(msg: dict[str, Any]) -> AccountUpdate | None:
    a = msg.get("a")
    if not isinstance(a, dict):
        return None
    event_time = to_int(msg.get("T") or msg.get("E"))
    positions = tuple(
        Position(
            symbol=str(p.get("s", "")),
            position_side=str(p.get("ps", "BOTH")),
            amount=dec(p.get("pa")),
            entry_price=dec(p.get("ep")),
            mark_price=ZERO,
            unrealized_pnl=dec(p.get("up")),
            leverage=0,
            update_time=event_time,
        )
        for p in a.get("P", [])
        if isinstance(p, dict)
    )
    return AccountUpdate(reason=str(a.get("m", "")), positions=positions, event_time=event_time)


# --------------------------------------------------------------------------- REST payloads


def parse_rest_order(item: dict[str, Any]) -> Order:
    return Order(
        order_id=to_int(item.get("orderId")),
        is_algo=False,
        symbol=str(item.get("symbol", "")),
        side=str(item.get("side", "")),
        position_side=str(item.get("positionSide", "BOTH")),
        type=str(item.get("type", "")),
        price=dec(item.get("price")),
        stop_price=dec(item.get("stopPrice")),
        qty=dec(item.get("origQty")),
        filled_qty=dec(item.get("executedQty")),
        status=str(item.get("status", "NEW")),
        reduce_only=to_bool(item.get("reduceOnly")) or to_bool(item.get("closePosition")),
        direction="",
        update_time=to_int(item.get("updateTime") or item.get("time")),
    )


def parse_rest_algo_order(item: dict[str, Any]) -> Order:
    return Order(
        order_id=to_int(item.get("algoId")),
        is_algo=True,
        symbol=str(item.get("symbol", "")),
        side=str(item.get("side", "")),
        position_side=str(item.get("positionSide", "BOTH")),
        type=str(item.get("orderType") or item.get("type") or ""),
        price=dec(item.get("price")),
        stop_price=dec(item.get("triggerPrice")),
        qty=dec(item.get("quantity")),
        filled_qty=ZERO,
        status=str(item.get("algoStatus", "NEW")),
        reduce_only=to_bool(item.get("reduceOnly")) or to_bool(item.get("closePosition")),
        direction="",
        update_time=to_int(item.get("updateTime") or item.get("createTime")),
    )


def parse_position_risk(
    items: Iterable[dict[str, Any]], *, include_flat: bool = False
) -> list[Position]:
    """Parse ``/fapi/v2|v3/positionRisk`` rows (tolerant to field differences)."""
    result: list[Position] = []
    for item in items:
        amount = dec(item.get("positionAmt"))
        if amount == 0 and not include_flat:
            continue
        pnl = item.get("unRealizedProfit", item.get("unrealizedProfit"))
        result.append(
            Position(
                symbol=str(item.get("symbol", "")),
                position_side=str(item.get("positionSide", "BOTH")),
                amount=amount,
                entry_price=dec(item.get("entryPrice")),
                mark_price=dec(item.get("markPrice")),
                unrealized_pnl=dec(pnl),
                leverage=to_int(item.get("leverage")),
                update_time=to_int(item.get("updateTime")),
            )
        )
    return result
