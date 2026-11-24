"""Pure message formatting (Telegram HTML + Pushover plain text)."""

from __future__ import annotations

import html
from decimal import Decimal

from binance_monitor.binance.models import ZERO, Direction, Order, Position, SymbolInfo
from binance_monitor.monitor.lifecycle import OrderChangeKind
from binance_monitor.monitor.notices import OrderNotice, PositionNotice

RESYNC_LABEL = "(detected on resync)"


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def fmt_num(value: Decimal, precision: int) -> str:
    """Format with thousands separators using the symbol precision (-1 = natural)."""
    if precision >= 0:
        return f"{value:,.{precision}f}"
    text = format(value.normalize(), ",f")
    return text


def fmt_qty(value: Decimal, precision: int) -> str:
    """Quantity at the symbol precision with trailing zeros trimmed (2.500 -> 2.5)."""
    text = fmt_num(value, precision)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_usdt(value: Decimal) -> str:
    return f"{value:,.2f}"


def fmt_signed(value: Decimal) -> str:
    return f"{value:+,.2f}"


def direction_label(direction: str, *, fallback: str = "") -> str:
    try:
        return Direction(direction).label
    except ValueError:
        return fallback or direction.replace("_", " ")


def _size(order_qty: Decimal, info: SymbolInfo) -> str:
    return f"{fmt_qty(order_qty, info.quantity_precision)} {info.base_asset}"


def format_order(notice: OrderNotice, info: SymbolInfo) -> str:
    o: Order = notice.order
    if notice.kind is OrderChangeKind.NEW:
        header = "🟢 NEW ORDER"
    elif notice.kind is OrderChangeKind.UPDATED:
        header = "✏️ ORDER UPDATED"
    else:
        header = f"❌ ORDER REMOVED ({notice.reason or 'unknown'})"
    title = f"<b>{esc(header)} · {esc(notice.account)}</b>"
    if notice.resync:
        title += f" {esc(RESYNC_LABEL)}"

    label = direction_label(o.direction, fallback=f"{o.side} {o.position_side}")
    line1 = f"{esc(o.symbol)} · {esc(label)} · {esc(o.type)}"
    if o.is_algo:
        line1 += " · conditional"

    if o.price > 0:
        price_text = fmt_num(o.price, info.price_precision)
    elif notice.ref_price > 0:
        price_text = f"market (~{fmt_num(notice.ref_price, info.price_precision)})"
    else:
        price_text = "market"
    parts = [f"Price: {esc(price_text)}"]
    if o.stop_price > 0:
        parts.append(f"Stop: {esc(fmt_num(o.stop_price, info.price_precision))}")
    usdt = o.qty * notice.ref_price if notice.ref_price > 0 else None
    size = _size(o.qty, info)
    size_text = f"{size} ({fmt_usdt(usdt)} {info.quote_asset})" if usdt is not None else size
    parts.append(f"Size: {esc(size_text)}")
    if o.filled_qty > 0 and notice.kind is not OrderChangeKind.NEW:
        parts.append(f"Filled: {esc(fmt_qty(o.filled_qty, info.quantity_precision))}")
    return "\n".join([title, line1, " · ".join(parts)])


def format_position(notice: PositionNotice, info: SymbolInfo) -> str:
    p = notice.position
    icon, word = ("📈", "OPENED") if notice.opened else ("📉", "CLOSED")
    title = f"<b>{icon} POSITION {word} · {esc(notice.account)}</b>"
    if notice.resync:
        title += f" {esc(RESYNC_LABEL)}"
    size = abs(p.amount)
    parts = [esc(p.symbol), esc(p.side_label)]
    size_text = f"{fmt_qty(size, info.quantity_precision)} {info.base_asset}"
    if notice.opened:
        notional = p.notional
        parts.append(esc(f"{size_text} ({fmt_usdt(notional)} {info.quote_asset})"))
        line = " · ".join(parts)
        line += f" @ {esc(fmt_num(p.entry_price, info.price_precision))}"
        if p.leverage:
            line += f" · {p.leverage}x"
    else:
        parts.append(esc(size_text))
        if notice.realized_pnl is not None:
            parts.append(
                f"realized PnL: {esc(fmt_signed(notice.realized_pnl))} {esc(info.quote_asset)}"
            )
        line = " · ".join(parts)
    return f"{title}\n{line}"


def pushover_text(notice: PositionNotice, info: SymbolInfo) -> tuple[str, str]:
    """(title, message) for Pushover (plain text, no HTML)."""
    p = notice.position
    word = "opened" if notice.opened else "closed"
    title = f"Position {word} · {notice.account}"
    size = f"{fmt_qty(abs(p.amount), info.quantity_precision)} {info.base_asset}"
    if notice.opened:
        message = (
            f"{p.symbol} {p.side_label} {size} ({fmt_usdt(p.notional)} {info.quote_asset})"
            f" @ {fmt_num(p.entry_price, info.price_precision)}"
        )
        if p.leverage:
            message += f" · {p.leverage}x"
    else:
        message = f"{p.symbol} {p.side_label} {size}"
        if notice.realized_pnl is not None:
            message += f" · realized PnL: {fmt_signed(notice.realized_pnl)} {info.quote_asset}"
    if notice.resync:
        message += f" {RESYNC_LABEL}"
    return title, message


def format_account_down(account: str, error: str) -> str:
    return (
        f"⚠️ <b>Account {esc(account)}: connection problem</b>\n"
        f"{esc(error)}\nRetrying with backoff; I will post again when it recovers."
    )


def format_account_recovered(account: str) -> str:
    return f"✅ <b>Account {esc(account)}: connection restored</b>"


__all__ = [
    "RESYNC_LABEL",
    "ZERO",
    "Position",
    "direction_label",
    "esc",
    "fmt_num",
    "fmt_qty",
    "fmt_signed",
    "fmt_usdt",
    "format_account_down",
    "format_account_recovered",
    "format_order",
    "format_position",
    "pushover_text",
]
