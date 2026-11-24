from decimal import Decimal

from binance_monitor.binance.models import SymbolInfo
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


def test_new_order_layout_matches_spec() -> None:
    n = OrderNotice(K.NEW, "main-acc", order(), Decimal("64250"))
    assert format_order(n, BTC).split("\n") == [
        "<b>🟢 NEW ORDER · main-acc</b>",
        "BTCUSDT · OPEN LONG · LIMIT",
        "Price: 64,250.00 · Size: 0.015 BTC (963.75 USDT)",
    ]


def test_updated_and_removed_headers() -> None:
    upd = format_order(OrderNotice(K.UPDATED, "a", order(), Decimal("64250")), BTC)
    assert "✏️ ORDER UPDATED · a" in upd
    rem = format_order(OrderNotice(K.REMOVED, "a", order(), Decimal("64250"), "canceled"), BTC)
    assert "❌ ORDER REMOVED (canceled) · a" in rem


def test_stop_price_type_and_market_price() -> None:
    o = order(
        type="STOP_MARKET", price="0", stop_price="63000", direction="CLOSE_LONG", is_algo=True
    )
    text = format_order(OrderNotice(K.NEW, "a", o, Decimal("63000")), BTC)
    assert "CLOSE LONG · STOP_MARKET · conditional" in text
    assert "Price: market (~63,000.00)" in text and "Stop: 63,000.00" in text


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
        "ETHUSDT · SHORT · 2.5 ETH (8,120.40 USDT) @ 3,248.16 · 10x",
    ]
    closed = format_position(PositionNotice(False, "main-acc", p, Decimal("42.18")), ETH)
    assert closed.split("\n")[0] == "<b>📉 POSITION CLOSED · main-acc</b>"
    assert "ETHUSDT · SHORT" in closed and "realized PnL: +42.18 USDT" in closed
    no_pnl = format_position(PositionNotice(False, "main-acc", p, None), ETH)
    assert "realized" not in no_pnl


def test_pushover_text_is_plain() -> None:
    p = pos("ETHUSDT", "-2.5", entry="3248.16", mark="3248.16", leverage=10)
    title, msg = pushover_text(PositionNotice(True, "main<acc>", p), ETH)
    assert title == "Position opened · main<acc>" and "<" not in msg
    assert msg.startswith("ETHUSDT SHORT 2.5 ETH")


def test_number_formatting_uses_precision() -> None:
    assert fmt_num(Decimal("1234.5"), 2) == "1,234.50"
    assert fmt_num(Decimal("0.0001"), 4) == "0.0001"
    assert fmt_num(Decimal("100"), -1) == "100"
    assert fmt_num(Decimal("0.50"), -1) == "0.5"
