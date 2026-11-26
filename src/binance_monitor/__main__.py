"""Entrypoint: load config, migrate DB, start bot + monitors, shut down gracefully."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramUnauthorizedError
from pydantic import ValidationError

from binance_monitor.binance.client import BinanceClient
from binance_monitor.binance.symbols import SymbolCache
from binance_monitor.binance.user_stream import websocket_connector
from binance_monitor.bot.admin import AdminHandlers
from binance_monitor.bot.router import build_router
from binance_monitor.bot.user import UserHandlers
from binance_monitor.config import Settings
from binance_monitor.crypto import Crypto
from binance_monitor.db.connection import Database, migrate
from binance_monitor.db.repositories import Repositories
from binance_monitor.monitor.manager import MonitorManager
from binance_monitor.notify.notifier import Notifier
from binance_monitor.notify.pinned import PinnedSummary
from binance_monitor.notify.pushover import PushoverBroadcaster, PushoverClient
from binance_monitor.notify.telegram_publisher import TelegramPublisher

log = logging.getLogger("binance_monitor")

HEARTBEAT_INTERVAL = 15.0


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        print("Invalid configuration (.env / environment):", file=sys.stderr)
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]).upper() or "settings"
            print(f"  - {where}: {err['msg']}", file=sys.stderr)
        sys.exit(2)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # httpx logs full request URLs (including signed query strings) at INFO.
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def heartbeat_loop(path: Path) -> None:
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(str(time.time()))
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("cannot write heartbeat: %s", exc)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def check_bot_rights(bot: Bot, chat_id: int) -> None:
    """Log (never fail) if the bot lacks the group admin rights the pinned message needs."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        if member.status == "creator":
            return
        if member.status != "administrator" or not getattr(member, "can_pin_messages", False):
            log.warning(
                "bot is not an admin with 'Pin messages' in chat %s: "
                "the pinned summary will not work",
                chat_id,
            )
    except Exception as exc:
        log.warning("could not check bot rights in chat %s: %s", chat_id, exc)


async def amain(settings: Settings) -> None:
    db = Database(settings.db_path)
    await db.connect()
    version = await migrate(db)
    log.info("database ready (schema v%d) at %s", version, settings.db_path)

    repos = Repositories(db)
    crypto = Crypto(settings.encryption_key)
    http = httpx.AsyncClient()
    session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_url))
    bot = Bot(token=settings.telegram_bot_token, session=session)

    symbols = SymbolCache(BinanceClient(settings.binance_fapi_url, "", "", http))
    publisher = TelegramPublisher(bot, settings.telegram_chat_id, settings.telegram_topic_id)
    pinned = PinnedSummary(repos, publisher, symbols.get)
    pushover = PushoverClient(
        http,
        settings.pushover_app_token,
        settings.pushover_api_url,
        priority=settings.pushover_priority,
        retry_seconds=settings.pushover_retry_seconds,
        expire_seconds=settings.pushover_expire_seconds,
    )
    notifier = Notifier(
        publisher,
        PushoverBroadcaster(pushover, repos.subscribers),
        symbols.get,
        pinned.request_update,
    )
    manager = MonitorManager(
        repos=repos,
        crypto=crypto,
        http=http,
        sink=notifier,
        stream_connector=websocket_connector,
        fapi_url=settings.binance_fapi_url,
        fstream_url=settings.binance_fstream_url,
        reconcile_seconds=settings.reconcile_seconds,
    )
    admin = AdminHandlers(
        admin_ids=set(settings.admin_ids),
        repos=repos,
        crypto=crypto,
        manager=manager,
        on_state_changed=pinned.request_update,
    )
    user = UserHandlers(subscribers=repos.subscribers, pushover=pushover)
    dp = Dispatcher()
    dp.include_router(build_router(admin, user))

    background: list[asyncio.Task[None]] = []
    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            log.error("TELEGRAM_BOT_TOKEN was rejected by Telegram")
            sys.exit(1)
        except Exception as exc:
            log.error("cannot reach Telegram at startup: %s", type(exc).__name__)
            sys.exit(1)
        log.info("Telegram bot @%s ready", me.username)
        await check_bot_rights(bot, settings.telegram_chat_id)

        with contextlib.suppress(Exception):
            await asyncio.wait_for(symbols.refresh(), 20)

        background = [
            asyncio.create_task(heartbeat_loop(settings.heartbeat_path), name="heartbeat"),
            asyncio.create_task(symbols.run(), name="symbols"),
            asyncio.create_task(pinned.run(), name="pinned"),
            asyncio.create_task(
                manager.refresh_marks_loop(settings.pinned_refresh_seconds), name="marks"
            ),
        ]
        await manager.start_all()
        log.info("polling for Telegram updates")
        await dp.start_polling(bot, handle_signals=True)  # returns on SIGTERM / SIGINT
    finally:
        log.info("shutting down")
        await manager.stop_all()  # closes listenKeys; cancelling a monitor closes its WebSocket
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await http.aclose()
        await bot.session.close()
        await db.close()
        log.info("bye")


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(amain(settings))


if __name__ == "__main__":
    main()
