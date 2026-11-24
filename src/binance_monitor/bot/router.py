"""Wire handlers into aiogram routers (private chat commands; group redirects)."""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from binance_monitor.bot.admin import AdminHandlers
from binance_monitor.bot.filters import ADD_ACCOUNT_DASH_RE, PRIVATE_ONLY_RE, safe_delete
from binance_monitor.bot.user import UserHandlers

_ADD_RE = re.compile(r"^/add[_-]account(@\w+)?(\s|$)")


async def group_redirect(message: Message) -> None:
    """In groups the bot only posts; private-only commands get a short pointer."""
    if message.text and _ADD_RE.match(message.text):
        await safe_delete(message)  # someone pasted credentials into the group
    await message.reply("Please DM me for this command.")


def build_router(admin: AdminHandlers, user: UserHandlers) -> Router:
    router = Router(name="binance-monitor")

    private = Router(name="private")
    private.message.filter(F.chat.type == ChatType.PRIVATE)
    private.message.register(user.start, CommandStart())
    private.message.register(user.start, Command("help"))
    private.message.register(user.subscribe, Command("subscribe"))
    private.message.register(user.unsubscribe, Command("unsubscribe"))
    private.message.register(admin.add_account, Command("add_account"))
    private.message.register(admin.add_account, F.text.regexp(ADD_ACCOUNT_DASH_RE))
    private.message.register(admin.accounts, Command("accounts"))
    private.message.register(admin.remove_account, Command("remove_account"))

    group = Router(name="group")
    group.message.filter(F.chat.type != ChatType.PRIVATE)
    group.message.register(group_redirect, F.text.regexp(PRIVATE_ONLY_RE))

    router.include_router(private)
    router.include_router(group)
    return router
