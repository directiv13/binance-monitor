"""Pure rendering of the pinned summary message (positions + open orders for all accounts)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from binance_monitor.binance.models import Order, Position, SymbolInfo
from binance_monitor.notify.formatting import (
    direction_label,
    esc,
    fmt_num,
    fmt_qty,
    fmt_signed,
    fmt_usdt,
)

TELEGRAM_LIMIT = 4096
HEADER = "📌 <b>Open positions &amp; orders</b>"


@dataclass(frozen=True, slots=True)
class AccountView:
    name: str
    status: str
    positions: list[Position]
    orders: list[Order]


@dataclass(frozen=True, slots=True)
class RenderedSummary:
    body: str  # everything except the timestamp footer: used to detect "nothing changed"
    footer: str

    @property
    def text(self) -> str:
        return f"{self.body}\n\n{self.footer}"


# detail levels, from richest to most compact
_FULL, _ORDERS_BY_SYMBOL, _COMPACT, _COUNTS = range(4)


def render_summary(
    views: list[AccountView],
    info: Callable[[str], SymbolInfo],
    now: datetime | None = None,
    limit: int = TELEGRAM_LIMIT,
) -> RenderedSummary:
    now = now or datetime.now(UTC)
    footer = f"Last updated: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    reserve = len(footer) + 2

    for level in (_FULL, _ORDERS_BY_SYMBOL, _COMPACT, _COUNTS):
        body = _body(views, info, level)
        if len(body) + reserve <= limit:
            return RenderedSummary(body, footer)

    # Last resort: keep whole account blocks (never cut inside a line) and say what was dropped.
    blocks = [_account_block(v, info, _COUNTS) for v in views]
    kept: list[str] = []
    for i, block in enumerate(blocks):
        remaining = len(blocks) - i - 1
        note = f"… and {remaining} more account(s) not shown" if remaining else ""
        candidate = "\n\n".join([HEADER, *kept, block] + ([note] if note else []))
        if len(candidate) + reserve > limit and kept:
            dropped = len(blocks) - len(kept)
            kept.append(f"… and {dropped} more account(s) not shown")
            break
        kept.append(block)
    return RenderedSummary("\n\n".join([HEADER, *kept]), footer)


def _body(views: list[AccountView], info: Callable[[str], SymbolInfo], level: int) -> str:
    if not views:
        return f"{HEADER}\n\n<i>No accounts configured.</i>"
    return "\n\n".join([HEADER, *(_account_block(v, info, level) for v in views)])


def _account_block(view: AccountView, info: Callable[[str], SymbolInfo], level: int) -> str:
    badge = "✅" if view.status == "connected" else ("⚠️" if view.status == "error" else "⏳")
    lines = [f"<b>{esc(view.name)}</b> {badge}"]
    if view.status == "error":
        lines.append("<i>connection problem, data may be stale</i>")

    if level == _COUNTS:
        lines.append(f"Positions: {len(view.positions)} · Open orders: {len(view.orders)}")
        return "\n".join(lines)

    lines.append("<u>Positions</u>")
    if view.positions:
        lines.extend(_position_line(p, info(p.symbol), level) for p in view.positions)
    else:
        lines.append("—")

    lines.append("<u>Open orders</u>")
    if not view.orders:
        lines.append("—")
    elif level == _FULL:
        lines.extend(_order_line(o, info(o.symbol)) for o in view.orders)
    else:
        counts = Counter(o.symbol for o in view.orders)
        lines.extend(f"• {esc(sym)}: {n} order(s)" for sym, n in sorted(counts.items()))
    return "\n".join(lines)


def _position_line(p: Position, info: SymbolInfo, level: int) -> str:
    size = fmt_qty(abs(p.amount), info.quantity_precision)
    pnl = fmt_signed(p.unrealized_pnl)
    if level >= _COMPACT:
        return f"• {esc(p.symbol)} {esc(p.side_label)} {esc(size)} · PnL {esc(pnl)}"
    text = (
        f"• {esc(p.symbol)} {esc(p.side_label)} {esc(size)} {esc(info.base_asset)}"
        f" ({esc(fmt_usdt(p.notional))} {esc(info.quote_asset)})"
        f" @ {esc(fmt_num(p.entry_price, info.price_precision))}"
    )
    if p.mark_price > 0:
        text += f" · mark {esc(fmt_num(p.mark_price, info.price_precision))}"
    text += f" · PnL {esc(pnl)}"
    if p.leverage:
        text += f" · {p.leverage}x"
    return text


def _order_line(o: Order, info: SymbolInfo) -> str:
    label = direction_label(o.direction, fallback=f"{o.side} {o.position_side}")
    price = fmt_num(o.price, info.price_precision) if o.price > 0 else "market"
    text = (
        f"• {esc(o.symbol)} {esc(label)} {esc(o.type)} "
        f"{esc(fmt_qty(o.qty, info.quantity_precision))} @ {esc(price)}"
    )
    if o.stop_price > 0:
        text += f" (stop {esc(fmt_num(o.stop_price, info.price_precision))})"
    if o.filled_qty > 0:
        text += f" · filled {esc(fmt_qty(o.filled_qty, info.quantity_precision))}"
    return text
