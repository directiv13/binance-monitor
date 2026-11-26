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


def _quote_size_line(value: Decimal, info: SymbolInfo) -> str:
    quote = esc(info.quote_asset)
    return f"💵Size in {quote}: {fmt_usdt(value)} {quote}"


def _side_icon(label: str) -> str:
    """🔴 for short, 🟢 for long; a flip counts as the side it ends on."""
    return "🔴" if label.endswith("SHORT") else "🟢"


def _order_header(notice: OrderNotice, label: str) -> str:
    """``🟢 CLOSE LONG ORDER`` / ``✏️ … UPDATED`` / ``🔴 … SHORT ORDER FILLED`` / ``❌ … CANCELED``.

    New, filled and triggered orders carry the side colour; ❌ is reserved for canceled orders.
    """
    if notice.kind is OrderChangeKind.NEW:
        return f"{_side_icon(label)} {label} ORDER"
    if notice.kind is OrderChangeKind.UPDATED:
        return f"✏️ {label} ORDER UPDATED"
    reason = notice.reason or "removed"
    if reason in ("filled", "triggered"):
        icon = _side_icon(label)
    else:
        icon = "❌" if reason == "canceled" else "⚠️"
    return f"{icon} {label} ORDER {reason.upper()}"


def format_order(notice: OrderNotice, info: SymbolInfo) -> str:
    o: Order = notice.order
    label = direction_label(o.direction, fallback=f"{o.side} {o.position_side}")
    title = f"<b>{esc(_order_header(notice, label))} · {esc(notice.account)}</b>"
    if notice.resync:
        title += f" {esc(RESYNC_LABEL)}"

    # The direction is already in the title; <code> makes the symbol tap-to-copy.
    symbol_line = f"<code>{esc(o.symbol)}</code> · {esc(o.type)}"
    if o.is_algo:
        symbol_line += " · conditional"

    if o.price > 0:
        price_text = fmt_num(o.price, info.price_precision)
    elif notice.ref_price > 0:
        price_text = f"market (~{fmt_num(notice.ref_price, info.price_precision)})"
    else:
        price_text = "market"
    lines = [f"💲Price: {esc(price_text)}"]
    if o.stop_price > 0:
        lines.append(f"🛑Stop: {esc(fmt_num(o.stop_price, info.price_precision))}")
    lines.append(f"🤑Size: {esc(_size(o.qty, info))}")
    if notice.ref_price > 0:
        lines.append(_quote_size_line(o.qty * notice.ref_price, info))
    if o.filled_qty > 0 and notice.kind is not OrderChangeKind.NEW:
        lines.append(f"✅Filled: {esc(fmt_qty(o.filled_qty, info.quantity_precision))}")
    return "\n".join([title, "", symbol_line, "", *lines])


def format_position(notice: PositionNotice, info: SymbolInfo) -> str:
    p = notice.position
    icon, word = ("📈", "OPENED") if notice.opened else ("📉", "CLOSED")
    title = f"<b>{icon} POSITION {word} · {esc(notice.account)}</b>"
    if notice.resync:
        title += f" {esc(RESYNC_LABEL)}"
    symbol_line = f"<code>{esc(p.symbol)}</code> · {esc(p.side_label)}"
    if p.leverage:
        symbol_line += f" · {p.leverage}x"
    size_line = f"🤑Size: {esc(_size(abs(p.amount), info))}"
    if notice.opened:
        lines = [
            f"💲Entry price: {esc(fmt_num(p.entry_price, info.price_precision))}",
            size_line,
            _quote_size_line(p.notional, info),
        ]
    else:
        lines = [size_line]
        if notice.realized_pnl is not None:
            pnl = f"{fmt_signed(notice.realized_pnl)} {info.quote_asset}"
            lines.append(f"💰Realized PnL: {esc(pnl)}")
    return "\n".join([title, "", symbol_line, "", *lines])


def pushover_text(notice: PositionNotice, info: SymbolInfo) -> tuple[str, str]:
    """(title, message) for Pushover (plain text, no HTML).

    Title: ``🟢 OPEN LONG · BTCUSDT · main-acc`` (green = long, red = short).
    Message: side and leverage, entry price, size in tokens and in USDT, then (after a blank
    line) notes: the realized PnL of a close and the resync label.
    """
    p = notice.position
    side = p.side_label
    action = "OPEN" if notice.opened else "CLOSE"
    title = f"{_side_icon(side)} {action} {side} · {p.symbol} · {notice.account}"
    lines = [
        f"{side} · {p.leverage}x" if p.leverage else side,
        f"Entry price: {fmt_num(p.entry_price, info.price_precision)}",
        f"Size: {_size(abs(p.amount), info)}",
        f"Size in {info.quote_asset}: {fmt_usdt(p.notional)}",
    ]
    notes: list[str] = []
    if notice.realized_pnl is not None and not notice.opened:
        notes.append(f"Realized PnL: {fmt_signed(notice.realized_pnl)} {info.quote_asset}")
    if notice.resync:
        notes.append(RESYNC_LABEL)
    if notes:
        lines += ["", *notes]
    return title, "\n".join(lines)


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
