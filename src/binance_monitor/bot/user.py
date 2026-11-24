"""User commands (private chat): Pushover subscription."""

from __future__ import annotations

import logging
from typing import Protocol

from aiogram.types import Message

from binance_monitor.bot.filters import parse_args, user_id_of
from binance_monitor.db.repositories import SubscriberRepository
from binance_monitor.notify.pushover import PushoverResult

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Binance monitor bot\n\n"
    "/subscribe <pushover_user_key> — get a Pushover push when a position opens or closes\n"
    "/unsubscribe — stop the pushes\n"
    "/help — this text\n\n"
    "Admins also have /add_account, /accounts and /remove_account.\n"
    "Orders and positions are posted in the group topic."
)


class PushoverValidator(Protocol):
    async def validate(self, user_key: str) -> PushoverResult: ...


class UserHandlers:
    def __init__(self, *, subscribers: SubscriberRepository, pushover: PushoverValidator) -> None:
        self._subscribers = subscribers
        self._pushover = pushover

    async def start(self, message: Message) -> None:
        await message.answer(HELP_TEXT)

    async def subscribe(self, message: Message) -> None:
        user_id = user_id_of(message)
        args = parse_args(message.text)
        if user_id is None or len(args) != 1:
            await message.answer("Usage: /subscribe <your Pushover user key>")
            return
        result = await self._pushover.validate(args[0])
        if result is PushoverResult.INVALID_USER:
            await message.answer("❌ Pushover says this user key is not valid. Nothing saved.")
            return
        if result is PushoverResult.ERROR:
            await message.answer(
                "⚠️ Could not validate the key with Pushover right now. Try again later."
            )
            return
        await self._subscribers.upsert(user_id, args[0])
        log.info("user %s subscribed to Pushover", user_id)
        await message.answer("✅ Subscribed. You will get a push when a position opens or closes.")

    async def unsubscribe(self, message: Message) -> None:
        user_id = user_id_of(message)
        if user_id is not None:
            await self._subscribers.remove(user_id)
        await message.answer("✅ Unsubscribed. You will no longer receive pushes.")
