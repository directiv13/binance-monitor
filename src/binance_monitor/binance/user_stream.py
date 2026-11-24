"""User-data-stream plumbing: URL building, WebSocket connector and reconnect backoff."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from websockets.asyncio.client import connect

# A connector takes a URL and yields an async iterator of raw text frames.
StreamConnector = Callable[[str], AbstractAsyncContextManager[AsyncIterator[Any]]]

# Binance closes a connection after 24h and a listenKey lives 60 min unless kept alive.
KEEPALIVE_SECONDS = 30 * 60
MAX_CONNECTION_AGE_SECONDS = 23 * 3600


def build_stream_url(fstream_url: str, listen_key: str) -> str:
    """Private user-data stream URL (the legacy ``/ws/<key>`` path was retired by Binance)."""
    return f"{fstream_url.rstrip('/')}/private/ws/{listen_key}"


@asynccontextmanager
async def websocket_connector(url: str) -> AsyncIterator[AsyncIterator[Any]]:
    async with connect(url, ping_interval=20, ping_timeout=30, max_size=2**22) as ws:
        yield aiter(ws)


class Backoff:
    """Exponential backoff with jitter (half fixed, half random)."""

    def __init__(
        self,
        base: float = 1.0,
        cap: float = 300.0,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._base = base
        self._cap = cap
        self._rng = rng
        self._attempt = 0

    def next(self) -> float:
        delay = float(min(self._cap, self._base * (2**self._attempt)))
        self._attempt = min(self._attempt + 1, 32)
        return delay / 2 + self._rng() * (delay / 2)

    def reset(self) -> None:
        self._attempt = 0
