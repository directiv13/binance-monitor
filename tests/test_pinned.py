from datetime import UTC, datetime

import pytest

from binance_monitor.binance.models import SymbolInfo
from binance_monitor.db.repositories import Repositories
from binance_monitor.notify.pinned import KV_KEY, PinnedSummary
from binance_monitor.notify.telegram_publisher import MessageGoneError

from .conftest import order, pos


def info(symbol: str) -> SymbolInfo:
    return SymbolInfo(symbol, symbol.removesuffix("USDT"), "USDT", 2, 3)


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []
        self.edits: list[tuple[int, str]] = []
        self.pinned: list[int] = []
        self.gone = False
        self._next_id = 100

    async def send(self, text: str, *, disable_notification: bool = False) -> int:
        self._next_id += 1
        self.sent.append((text, disable_notification))
        return self._next_id

    async def edit(self, message_id: int, text: str) -> None:
        if self.gone:
            raise MessageGoneError("message to edit not found")
        self.edits.append((message_id, text))

    async def pin(self, message_id: int) -> None:
        self.pinned.append(message_id)


@pytest.fixture
def clock() -> list[datetime]:
    return [datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)]


def make(repos: Repositories, pub: FakePublisher, clock: list[datetime]) -> PinnedSummary:
    return PinnedSummary(repos, pub, info, min_interval=0, now=lambda: clock[0])


async def test_first_run_sends_silently_pins_and_stores_id(
    repos: Repositories, clock: list[datetime]
) -> None:
    pub = FakePublisher()
    await repos.accounts.create("main", "k", "s", None)
    summary = make(repos, pub, clock)
    assert await summary.update() is True
    assert len(pub.sent) == 1 and pub.sent[0][1] is True  # disable_notification
    assert pub.pinned == [101]
    assert await repos.kv.get(KV_KEY) == "101"


async def test_edit_on_change_and_skip_when_unchanged(
    repos: Repositories, clock: list[datetime]
) -> None:
    pub = FakePublisher()
    a = await repos.accounts.create("main", "k", "s", None)
    summary = make(repos, pub, clock)
    await summary.update()
    assert await summary.update() is False  # identical -> no edit
    clock[0] = datetime(2026, 11, 18, 12, 1, tzinfo=UTC)
    assert await summary.update() is False  # only the timestamp differs -> still no edit
    await repos.positions.upsert(a.id, pos())
    await repos.orders.upsert(a.id, order())
    assert await summary.update() is True
    assert pub.edits[0][0] == 101 and "BTCUSDT" in pub.edits[0][1]
    assert "Last updated: 2026-11-18 12:01:00 UTC" in pub.edits[0][1]


async def test_recreates_and_repins_when_message_was_deleted(
    repos: Repositories, clock: list[datetime]
) -> None:
    pub = FakePublisher()
    a = await repos.accounts.create("main", "k", "s", None)
    summary = make(repos, pub, clock)
    await summary.update()
    pub.gone = True
    await repos.positions.upsert(a.id, pos())
    await summary.update()
    assert len(pub.sent) == 2 and pub.pinned == [101, 102]
    assert await repos.kv.get(KV_KEY) == "102"


async def test_existing_message_id_survives_restart(
    repos: Repositories, clock: list[datetime]
) -> None:
    await repos.accounts.create("main", "k", "s", None)
    await repos.kv.set(KV_KEY, "55")
    pub = FakePublisher()
    await make(repos, pub, clock).update()
    assert pub.sent == [] and pub.edits[0][0] == 55  # edited, not re-sent
