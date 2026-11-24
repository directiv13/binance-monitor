# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python 3.12 asyncio service that watches Binance **USD-M Futures** accounts and reports to a Telegram supergroup topic (+ one pinned summary message) and to Pushover subscribers. Accounts are added/removed at runtime via bot commands in a private chat. The Binance client is in-house (no ccxt / python-binance). `README.md` documents behaviour, Binance API notes, deployment and security; `PROMPT.md` is the original build spec.

## Commands

```bash
pip install -e ".[dev]"                      # Windows venv: .venv\Scripts\activate
ruff check . && ruff format --check . && mypy && pytest   # exactly what CI runs
pytest tests/test_account_monitor.py::test_name           # single test
ruff format .                                              # auto-format
python -m binance_monitor                                  # run (needs .env, see .env.example)
docker compose up --build                                  # run in Docker
```

- `mypy` runs in **strict** mode over `src` *and* `tests` — tests must be fully typed too.
- pytest uses `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed); `pythonpath = ["src"]`.
- Ruff line length is 100; the RUF001–003 (ambiguous unicode) rules are intentionally off.
- Dependencies are pinned exactly in `pyproject.toml`.
- Tests never touch the network: Binance/Pushover REST via `respx`, Telegram calls recorded, WebSocket tested against a local server. Payload fixtures are in `tests/fixtures/samples.json`; `tests/conftest.py` has the helpers (`sample`, `order_event`, `algo_event`, `account_event`, `pos`, `order`, the `repos` fixture on a temp SQLite DB, and `RecordingSink`).

## Architecture

Wiring happens in `__main__.amain`: Settings → `Database`/`migrate` → `Repositories` → `Notifier` (Telegram publisher + Pushover broadcaster + `PinnedSummary`) → `MonitorManager` → aiogram `Dispatcher` (long polling). Background tasks: heartbeat file (Docker healthcheck), symbol cache, pinned-summary updater, periodic mark-price refresh. Shutdown order matters: monitors stop first (releases listenKeys / closes WebSockets), then the DB checkpoints and closes.

**Monitor layer (`monitor/`)** is the core and is split into pure logic vs I/O:
- Pure, easy to unit-test: `direction.py` (Open/Close Long/Short/Flip, effective USDT price), `lifecycle.py` (maps a stream `OrderUpdate` to NEW/UPDATED/REMOVED/PROGRESS), `diff.py` (snapshot-vs-DB diffs for orders and positions).
- `account_monitor.py`: one `AccountMonitor` per account. `run()` is a supervisor loop (session → failure → exponential backoff + jitter). A session = sync time → create listenKey → open WebSocket → `reconcile()` → run reader, keepalive, periodic reconcile and 23h max-age guard concurrently (`_run_until_first_done`). A single `asyncio.Lock` serializes stream-event handling and reconciliation. Handlers must be **idempotent**, because frames received during the snapshot stay buffered in the socket and are replayed after it.
- `manager.py`: `MonitorManager` owns one asyncio task per account and starts/stops them at runtime (used by `/add_account` / `/remove_account`). One account failing must never affect the others.
- `notices.py`: `EventSink` protocol + notice dataclasses — the boundary between monitors and `notify/`. Tests plug in `RecordingSink`; `AccountMonitor` talks to the Binance client through the narrow `AccountClient` protocol so it can be faked.

**Semantics worth knowing before changing behaviour** (details in README "What gets posted"):
- An order's *direction* is computed once at creation and persisted; later messages reuse it.
- Partial fills do not post (they only update DB + pinned message).
- Conditional orders (STOP/TAKE_PROFIT/TRAILING) come from Binance's **Algo Service** (separate REST endpoints and the `ALGO_UPDATE` event), stored with `is_algo = 1`. Order identity is `(order_id, is_algo)`. If the open-algo-orders endpoint is unavailable (`None`), reconciliation leaves stored algo orders alone.
- The very first snapshot of a new account is stored **silently** (guarded by the `seeded:<account_id>` key in the kv table); restarts never re-post existing state. Later reconciliation posts anything missed marked `(detected on resync)`.
- User data stream URL is `wss://fstream.binance.com/private/ws/<listenKey>`; listenKey keepalive every 30 min, reconnect at 23h; `-1125` and `listenKeyExpired` trigger a planned reconnect (no warning, no backoff).

**Other layers:**
- `binance/`: `client.py` (HMAC-SHA256 signed REST, server-time offset, one retry on `-1021`, honours `Retry-After` on 429/418), `events.py` (stream message parsing, tolerant of missing fields), `models.py` (Decimal-based dataclasses), `user_stream.py` (WebSocket connector, `Backoff`, keepalive constants), `symbols.py` (cached symbol metadata).
- `notify/`: `formatting.py` (message text), `notifier.py` (the real `EventSink`), `pinned.py` (debounced ≥3 s, skips unchanged text, degrades gracefully under Telegram's 4096-char limit), `pushover.py`, `telegram_publisher.py`.
- `bot/`: aiogram handlers. Admin commands require `ADMIN_IDS` + private chat; `/add_account` deletes the user's message *before* anything else (it contains secrets) and must never echo keys. `filters.py`/`router.py` also handle the `/add-account` spelling.
- `db/`: one shared aiosqlite connection (WAL); multi-statement writes go through `Database.transaction()`. Schema changes = a new numbered file in `db/migrations/` (`NNNN_name.sql`, applied at startup in order and tracked in `schema_version`); migrations ship in the wheel. All data access goes through `repositories.py`.
- `crypto.py`: Fernet encryption of stored API key/secret. `config.py`: pydantic-settings; invalid config prints a list of problems and exits with code 2.

## Conventions

- Never log signed URLs or credentials: `httpx`/`websockets` loggers are forced to WARNING, and `describe_error` masks `signature=…`. Keep that when adding logging.
- Money/quantities are `Decimal`, never `float`.
- Deployment: CI (`.github/workflows/ci-cd.yml`) tests, builds the image to GHCR on `main`, then SSH-deploys with `deploy/docker-compose.prod.yml`; the `.env` exists only on the VPS.
