"""SQLite connection (single shared connection, WAL) and a tiny migration runner."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")


class Database:
    """One shared aiosqlite connection. Multi-statement writes use :meth:`transaction`."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # best effort on shutdown
                log.debug("wal checkpoint failed", exc_info=True)
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialize writers and commit/rollback as one unit."""
        async with self._lock:
            conn = self.conn
            try:
                yield conn
            except BaseException:
                await conn.rollback()
                raise
            else:
                await conn.commit()


def _discover_migrations() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for entry in resources.files("binance_monitor.db.migrations").iterdir():
        match = _MIGRATION_RE.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(found)


async def migrate(db: Database) -> int:
    """Apply numbered SQL migrations newer than ``schema_version``. Returns the final version."""
    conn = db.conn
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    await conn.commit()
    async with conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row else 0

    for version, name, sql in _discover_migrations():
        if version <= current:
            continue
        log.info("applying migration %s", name)
        # executescript() manages its own transaction; make the step atomic explicitly.
        await conn.executescript(
            f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});\nCOMMIT;"
        )
        current = version
    return current
