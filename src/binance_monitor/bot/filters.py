"""Shared helpers for bot handlers."""

from __future__ import annotations

import logging
import re

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

log = logging.getLogger(__name__)

# Private-only commands (the group gets a "please DM me" reply for these).
PRIVATE_ONLY_RE = (
    r"^/(add[_-]account|accounts|remove[_-]account|subscribe|unsubscribe)(@\w+)?(\s|$)"
)
ADD_ACCOUNT_DASH_RE = r"^/add-account(@\w+)?(\s|$)"
ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def parse_args(text: str | None) -> list[str]:
    """Arguments after the command word (works for /cmd, /cmd@bot and /add-account)."""
    if not text:
        return []
    return text.split()[1:]


def user_id_of(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def safe_delete(message: Message) -> bool:
    """Delete a user's message; never raises (the bot may lack the right to delete)."""
    try:
        await message.delete()
        return True
    except TelegramAPIError as exc:
        log.warning("could not delete message in chat %s: %s", message.chat.id, exc)
        return False
