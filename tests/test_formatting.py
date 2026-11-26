from decimal import Decimal

import pytest

from binance_monitor.binance.models import Order, SymbolInfo
from binance_monitor.monitor.lifecycle import OrderChangeKind as K
from binance_monitor.monitor.notices import OrderNotice, PositionNotice
from binance_monitor.notify.formatting import (
    esc,
    fmt_num,
    format_account_down,
    format_order,
    format_position,
    pushover_text,
)

from .conftest import order, pos

BTC = SymbolInfo("BTCUSDT", "BTC", "USDT", 2, 3)
ETH = SymbolInfo("ETHUSDT", "ETH", "USDT", 2, 3)


SKHYNIX = SymbolInfo("SKHYNIXUSDT", "SKHYNIX", "USDT", 5, 2)


def _skhynix_close_long(filled_qty: str = "0") -> Order:
    return order(
        symbol="SKHYNIXUSDT",
        type="MARKET",
        price="0",
        qty="0.06",
        filled_qty=filled_qty,
        direction="CLOSE_LONG",
    )


def test_new_order_layout_matches_spec() -> None:
    n = OrderNotice(K.NEW, "main-acc", order(), Decimal("64250"))
    assert format_order(n, BTC).split("\n") == [
        "<b>🟢 OPEN LONG ORDER · main-acc</b>",
        "",
        "<code>BTCUSDT</code> · LIMIT",
        "",
        "💲Price: 64,250.00",
        "🤑Size: 0.015 BTC",
        "💵Size in USDT: 963.75 USDT",
    ]


def test_filled_order_layout_matches_spec() -> None:
    o = _skhynix_close_long(filled_qty="0.06")
    n = OrderNotice(K.REMOVED, "main-acc", o, Decimal("741.70"), "filled")
    assert format_order(n, SKHYNIX).split("\n") == [
        "<b>🟢 CLOSE LONG ORDER FILLED · main-acc</b>",
        "",
        "<code>SKHYNIXUSDT</code> · MARKET",
        "",
        "💲Price: market (~741.70000)",
        "🤑Size: 0.06 SKHYNIX",
        "💵Size in USDT: 44.50 USDT",
        "✅Filled: 0.06",
    ]


def test_order_without_reference_price_omits_usdt_size() -> None:
    n = OrderNotice(K.NEW, "a", _skhynix_close_long(), Decimal("0"))
    assert format_order(n, SKHYNIX).split("\n")[-2:] == [
        "💲Price: market",
        "🤑Size: 0.06 SKHYNIX",
    ]


def test_updated_and_removed_headers() -> None:
    upd = format_order(OrderNotice(K.UPDATED, "a", order(), Decimal("64250")), BTC)
    assert upd.split("\n")[0] == "<b>✏️ OPEN LONG ORDER UPDATED · a</b>"
    assert upd.split("\n")[2] == "<code>BTCUSDT</code> · LIMIT"

    def removed(reason: str | None) -> str:
        n = OrderNotice(K.REMOVED, "a", order(), Decimal("64250"), reason)
        return format_order(n, BTC).split("\n")[0]

    assert removed("canceled") == "<b>❌ OPEN LONG ORDER CANCELED · a</b>"
    assert removed("filled") == "<b>🟢 OPEN LONG ORDER FILLED · a</b>"
    assert removed("triggered") == "<b>🟢 OPEN LONG ORDER TRIGGERED · a</b>"
    # ❌ is for canceled orders only
    assert removed("expired") == "<b>⚠️ OPEN LONG ORDER EXPIRED · a</b>"
    assert removed("rejected") == "<b>⚠️ OPEN LONG ORDER REJECTED · a</b>"
    assert removed("no longer open") == "<b>⚠️ OPEN LONG ORDER NO LONGER OPEN · a</b>"
    assert removed(None) == "<b>⚠️ OPEN LONG ORDER REMOVED · a</b>"


def test_unknown_direction_falls_back_to_side() -> None:
    o = order(direction="", side="SELL", position_side="SHORT")
    text = format_order(OrderNotice(K.NEW, "a", o, Decimal("1")), BTC)
    assert text.split("\n")[0] == "<b>🔴 SELL SHORT ORDER · a</b>"


@pytest.mark.parametrize(
    ("direction", "icon"),
    [
        ("OPEN_LONG", "🟢"),
        ("CLOSE_LONG", "🟢"),
        ("OPEN_SHORT", "🔴"),
        ("CLOSE_SHORT", "🔴"),
        ("FLIP_LONG_SHORT", "🔴"),
        ("FLIP_SHORT_LONG", "🟢"),
    ],
)
def test_order_icon_follows_side(direction: str, icon: str) -> None:
    def header(kind: K, reason: str | None = None) -> str:
        n = OrderNotice(kind, "a", order(direction=direction), Decimal("64250"), reason)
        return format_order(n, BTC).split("\n")[0]

    assert header(K.NEW).startswith(f"<b>{icon} ")
    assert header(K.REMOVED, "filled").startswith(f"<b>{icon} ")
    assert header(K.REMOVED, "triggered").startswith(f"<b>{icon} ")
    assert header(K.REMOVED, "canceled").startswith("<b>❌ ")


def test_stop_price_type_and_market_price() -> None:
    o = order(
        type="STOP_MARKET", price="0", stop_price="63000", direction="CLOSE_LONG", is_algo=True
    )
    text = format_order(OrderNotice(K.NEW, "a", o, Decimal("63000")), BTC)
    assert text.split("\n")[0] == "<b>🟢 CLOSE LONG ORDER · a</b>"
    assert "<code>BTCUSDT</code> · STOP_MARKET · conditional" in text
    assert text.split("\n")[4:6] == ["💲Price: market (~63,000.00)", "🛑Stop: 63,000.00"]


def test_resync_label() -> None:
    n = OrderNotice(K.NEW, "a", order(), Decimal("64250"), resync=True)
    assert "(detected on resync)" in format_order(n, BTC).split("\n")[0]


def test_html_is_escaped() -> None:
    n = OrderNotice(K.NEW, "<script>&", order(symbol="A<B"), Decimal("1"))
    text = format_order(n, SymbolInfo("A<B", "A<", "USDT", 2, 3))
    assert "<script>" not in text and "&lt;script&gt;&amp;" in text
    assert "A&lt;B" in text
    assert "<b>" in text  # our own markup survives
    assert esc("a<b>&") == "a&lt;b&gt;&amp;"
    assert "&lt;i&gt;x" in format_account_down("acc", "<i>x")


def test_position_opened_and_closed() -> None:
    p = pos("ETHUSDT", "-2.5", entry="3248.16", mark="3248.16", leverage=10)
    opened = format_position(PositionNotice(True, "main-acc", p), ETH)
    assert opened.split("\n") == [
        "<b>📈 POSITION OPENED · main-acc</b>",
        "",
        "<code>ETHUSDT</code> · SHORT · 10x",
        "",
        "💲Entry price: 3,248.16",
        "🤑Size: 2.5 ETH",
        "💵Size in USDT: 8,120.40 USDT",
    ]
    closed = format_position(PositionNotice(False, "main-acc", p, Decimal("42.18")), ETH)
    assert closed.split("\n") == [
        "<b>📉 POSITION CLOSED · main-acc</b>",
        "",
        "<code>ETHUSDT</code> · SHORT · 10x",
        "",
        "🤑Size: 2.5 ETH",
        "💰Realized PnL: +42.18 USDT",
    ]
    no_pnl = format_position(PositionNotice(False, "main-acc", p, None), ETH)
    assert "Realized" not in no_pnl


def test_position_without_leverage_and_with_resync() -> None:
    p = pos("SKHYNIXUSDT", "0.15", entry="735.71", mark="732.87", leverage=0)
    text = format_position(PositionNotice(True, "a", p, resync=True), SKHYNIX)
    assert text.split("\n") == [
        "<b>📈 POSITION OPENED · a</b> (detected on resync)",
        "",
        "<code>SKHYNIXUSDT</code> · LONG",
        "",
        "💲Entry price: 735.71000",
        "🤑Size: 0.15 SKHYNIX",
        "💵Size in USDT: 109.93 USDT",
    ]


def test_pushover_text_open_short_is_plain() -> None:
    p = pos("ETHUSDT", "-2.5", entry="3248.16", mark="3248.16", leverage=10)
    title, msg = pushover_text(PositionNotice(True, "main<acc>", p), ETH)
    assert title == "🔴 OPEN SHORT · ETHUSDT · main<acc>"
    assert msg.split("\n") == [
        "SHORT · 10x",
        "Entry price: 3,248.16",
        "Size: 2.5 ETH",
        "Size in USDT: 8,120.40",
    ]  # no notes -> no trailing blank line
    assert "<" not in msg


def test_pushover_text_close_long_with_notes() -> None:
    p = pos("BTCUSDT", "0.015", entry="64250", mark="64250", leverage=5)
    title, msg = pushover_text(PositionNotice(False, "main-acc", p, Decimal("42.18")), BTC)
    assert title == "🟢 CLOSE LONG · BTCUSDT · main-acc"
    assert msg.split("\n") == [
        "LONG · 5x",
        "Entry price: 64,250.00",
        "Size: 0.015 BTC",
        "Size in USDT: 963.75",
        "",
        "Realized PnL: +42.18 USDT",
    ]
    _, both = pushover_text(PositionNotice(False, "main-acc", p, Decimal("-1"), resync=True), BTC)
    assert both.split("\n")[-3:] == ["", "Realized PnL: -1.00 USDT", "(detected on resync)"]
    _, resync = pushover_text(PositionNotice(True, "main-acc", p, resync=True), BTC)
    assert resync.split("\n")[-2:] == ["", "(detected on resync)"]


def test_pushover_text_without_leverage_shows_side_only() -> None:
    p = pos("BTCUSDT", "0.015", leverage=0)
    _, msg = pushover_text(PositionNotice(True, "a", p), BTC)
    assert msg.split("\n")[0] == "LONG"


def test_number_formatting_uses_precision() -> None:
    assert fmt_num(Decimal("1234.5"), 2) == "1,234.50"
    assert fmt_num(Decimal("0.0001"), 4) == "0.0001"
    assert fmt_num(Decimal("100"), -1) == "100"
    assert fmt_num(Decimal("0.50"), -1) == "0.5"
