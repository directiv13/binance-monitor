from datetime import UTC, datetime

from binance_monitor.binance.models import SymbolInfo
from binance_monitor.notify.summary import AccountView, render_summary

from .conftest import order, pos

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


def info(symbol: str) -> SymbolInfo:
    return SymbolInfo(symbol, symbol.removesuffix("USDT"), "USDT", 2, 3)


def balanced(text: str) -> bool:
    return all(line.count("<") == line.count(">") for line in text.split("\n"))


def test_renders_sections_and_timestamp() -> None:
    orders = [order(1), order(2, type="STOP_MARKET", stop_price="60000")]
    r = render_summary([AccountView("main", "connected", [pos()], orders)], info, NOW)
    assert "<b>main</b> ✅" in r.text
    assert "<u>Positions</u>" in r.text and "<u>Open orders</u>" in r.text
    assert "BTCUSDT LONG 0.015 BTC" in r.text and "mark 64,300.00" in r.text and "10x" in r.text
    assert "stop 60,000.00" in r.text
    assert r.text.endswith("Last updated: 2026-11-18 12:00:00 UTC")
    assert "Last updated" not in r.body


def test_empty_sections_and_error_badge() -> None:
    r = render_summary([AccountView("a", "error", [], [])], info, NOW)
    assert "⚠️" in r.text and "connection problem" in r.text and "—" in r.text
    assert "No accounts" in render_summary([], info, NOW).text


def test_body_stable_between_renders() -> None:
    views = [AccountView("a", "connected", [pos()], [order()])]
    later = datetime(2026, 11, 18, 12, 5, tzinfo=UTC)
    assert render_summary(views, info, NOW).body == render_summary(views, info, later).body


def test_over_4096_summarizes_orders_per_symbol_without_cutting_lines() -> None:
    orders = [order(i, symbol=f"COIN{i % 40}USDT") for i in range(400)]
    r = render_summary([AccountView("big", "connected", [pos()], orders)], info, NOW)
    assert len(r.text) <= 4096
    assert "10 order(s)" in r.text  # 400 orders / 40 symbols
    assert "BTCUSDT LONG" in r.text  # positions stay detailed at this level
    assert balanced(r.text)


def test_over_4096_with_many_accounts_drops_whole_blocks() -> None:
    views = [
        AccountView(
            f"account-{i:03d}",
            "connected",
            [pos(f"SYM{j}USDT") for j in range(5)],
            [order(j) for j in range(20)],
        )
        for i in range(120)
    ]
    r = render_summary(views, info, NOW)
    assert len(r.text) <= 4096
    assert "more account(s) not shown" in r.text
    assert balanced(r.text)
