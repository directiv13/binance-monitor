"""Pure order-direction logic (Open/Close/Flip) for hedge and one-way mode."""

from __future__ import annotations

from decimal import Decimal

from binance_monitor.binance.models import ZERO, Direction


def compute_direction(
    *,
    side: str,
    position_side: str,
    reduce_only: bool,
    close_position: bool,
    qty: Decimal,
    position_amount: Decimal,
    hedge_mode: bool = False,
) -> Direction:
    """Work out the direction label of an order at creation time.

    ``position_amount`` is the signed current position for the symbol, only used in one-way
    mode. Hedge mode is detected from the event's ``position_side`` (LONG/SHORT); the
    ``hedge_mode`` flag is only a fallback when the event carries no usable position side.
    """
    side = side.upper()
    ps = position_side.upper()
    is_buy = side == "BUY"

    if ps in ("LONG", "SHORT") or (ps not in ("BOTH",) and hedge_mode):
        if ps == "SHORT":
            return Direction.CLOSE_SHORT if is_buy else Direction.OPEN_SHORT
        return Direction.OPEN_LONG if is_buy else Direction.CLOSE_LONG

    # one-way mode
    if reduce_only or close_position:
        return Direction.CLOSE_SHORT if is_buy else Direction.CLOSE_LONG

    pos = position_amount
    if pos == ZERO:
        return Direction.OPEN_LONG if is_buy else Direction.OPEN_SHORT
    if (is_buy and pos > 0) or (not is_buy and pos < 0):
        return Direction.OPEN_LONG if is_buy else Direction.OPEN_SHORT
    # opposite side
    if qty <= abs(pos):
        return Direction.CLOSE_SHORT if is_buy else Direction.CLOSE_LONG
    return Direction.FLIP_SHORT_LONG if is_buy else Direction.FLIP_LONG_SHORT


def effective_price(
    *,
    price: Decimal,
    avg_price: Decimal,
    last_fill_price: Decimal,
    stop_price: Decimal,
    mark_price: Decimal,
) -> Decimal:
    """Price used for the USDT size: limit price, else avg/last fill, else trigger, else mark."""
    for candidate in (price, avg_price, last_fill_price, stop_price, mark_price):
        if candidate > 0:
            return candidate
    return ZERO


def size_usdt(price: Decimal, qty: Decimal) -> Decimal:
    return price * qty
