"""Posting to the configured Telegram group topic (send / edit / pin)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

log = logging.getLogger(__name__)

T = TypeVar("T")

_GONE_MARKERS = (
    "message to edit not found",
    "message_id_invalid",
    "message can't be edited",
    "message to pin not found",
)


class MessageGoneError(Exception):
    """The message we wanted to edit no longer exists."""


class TelegramPublisher:
    def __init__(self, bot: Bot, chat_id: int, topic_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._topic_id = topic_id
        self._send_lock = asyncio.Lock()  # keeps messages in order

    async def _with_retry(self, call: Callable[[], Awaitable[T]], attempts: int = 4) -> T:
        for attempt in range(attempts):
            try:
                return await call()
            except TelegramRetryAfter as exc:
                if attempt == attempts - 1:
                    raise
                log.warning("Telegram flood control: sleeping %ss", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 0.5)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def send(self, text: str, *, disable_notification: bool = False) -> int:
        async with self._send_lock:
            message = await self._with_retry(
                lambda: self._bot.send_message(
                    chat_id=self._chat_id,
                    message_thread_id=self._topic_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    disable_notification=disable_notification,
                )
            )
        return message.message_id

    async def edit(self, message_id: int, text: str) -> None:
        """Edit a message; silently ignores "message is not modified"."""
        try:
            await self._with_retry(
                lambda: self._bot.edit_message_text(
                    text=text,
                    chat_id=self._chat_id,
                    message_id=message_id,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            )
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message is not modified" in lowered:
                return
            if any(marker in lowered for marker in _GONE_MARKERS):
                raise MessageGoneError(str(exc)) from exc
            raise

    async def pin(self, message_id: int) -> None:
        """Pin silently. Needs the 'Pin messages' admin right; failures are only logged."""
        try:
            await self._with_retry(
                lambda: self._bot.pin_chat_message(
                    chat_id=self._chat_id, message_id=message_id, disable_notification=True
                )
            )
        except Exception as exc:
            log.warning(
                "could not pin message %s (does the bot have 'Pin messages'?): %s", message_id, exc
            )
