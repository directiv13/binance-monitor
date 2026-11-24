"""Admin commands (private chat, ADMIN_IDS only): add / list / remove Binance accounts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from aiogram.types import Message

from binance_monitor.binance.client import BinanceAPIError
from binance_monitor.binance.models import Account
from binance_monitor.bot.filters import (
    ACCOUNT_NAME_RE,
    parse_args,
    safe_delete,
    user_id_of,
)
from binance_monitor.crypto import Crypto, mask
from binance_monitor.db.repositories import DuplicateAccountError, Repositories

log = logging.getLogger(__name__)

NOT_ALLOWED = "⛔ You are not allowed to use this command."


class AccountManager(Protocol):
    async def validate_credentials(self, api_key: str, api_secret: str) -> None: ...
    async def start(self, account: Account) -> None: ...
    async def stop(self, account_id: int) -> None: ...


class AdminHandlers:
    def __init__(
        self,
        *,
        admin_ids: set[int],
        repos: Repositories,
        crypto: Crypto,
        manager: AccountManager,
        on_state_changed: Callable[[], None],
    ) -> None:
        self._admin_ids = admin_ids
        self._repos = repos
        self._crypto = crypto
        self._manager = manager
        self._on_state_changed = on_state_changed

    def _is_admin(self, message: Message) -> bool:
        return user_id_of(message) in self._admin_ids

    async def _deny(self, message: Message, command: str) -> None:
        log.warning("non-admin user %s tried /%s", user_id_of(message), command)
        await message.answer(NOT_ALLOWED)

    # ------------------------------------------------------------------ /add_account

    async def add_account(self, message: Message) -> None:
        # The message contains secrets: delete it first, whatever happens next.
        await safe_delete(message)
        if not self._is_admin(message):
            await self._deny(message, "add_account")
            return

        args = parse_args(message.text)
        if len(args) != 3:
            await message.answer("Usage: /add_account <name> <api_key> <secret>")
            return
        name, api_key, api_secret = args
        if not ACCOUNT_NAME_RE.fullmatch(name):
            await message.answer("Invalid name: use 1-32 characters from A-Z a-z 0-9 _ -")
            return
        if await self._repos.accounts.get_by_name(name) is not None:
            await message.answer(f"An account named '{name}' already exists.")
            return

        try:
            await self._manager.validate_credentials(api_key, api_secret)
        except BinanceAPIError as exc:
            await message.answer(f"❌ Binance rejected the credentials: {exc.code} {exc.msg}")
            return
        except Exception as exc:
            log.warning("credential check for '%s' failed: %s", name, type(exc).__name__)
            await message.answer("❌ Could not reach Binance to verify the credentials. Try again.")
            return

        try:
            account = await self._repos.accounts.create(
                name,
                self._crypto.encrypt(api_key),
                self._crypto.encrypt(api_secret),
                user_id_of(message),
            )
        except DuplicateAccountError:
            await message.answer(f"An account named '{name}' already exists.")
            return

        log.info("account '%s' added by %s (key %s)", name, user_id_of(message), mask(api_key))
        await self._manager.start(account)
        self._on_state_changed()
        await message.answer(
            f"✅ Account '{name}' added and monitoring started.\n"
            "Your message with the credentials was deleted."
        )

    # ------------------------------------------------------------------ /accounts

    async def accounts(self, message: Message) -> None:
        if not self._is_admin(message):
            await self._deny(message, "accounts")
            return
        accounts = await self._repos.accounts.list_all()
        if not accounts:
            await message.answer("No accounts yet. Use /add_account <name> <api_key> <secret>.")
            return
        lines = []
        for a in accounts:
            line = f"• {a.name} — {a.status} (added {a.created_at} UTC)"
            if a.status == "error" and a.last_error:
                line += f"\n   {a.last_error[:120]}"
            lines.append(line)
        await message.answer("\n".join(lines))

    # ------------------------------------------------------------------ /remove_account

    async def remove_account(self, message: Message) -> None:
        if not self._is_admin(message):
            await self._deny(message, "remove_account")
            return
        args = parse_args(message.text)
        if len(args) != 1:
            await message.answer("Usage: /remove_account <name>")
            return
        account = await self._repos.accounts.get_by_name(args[0])
        if account is None:
            await message.answer(f"No account named '{args[0]}'.")
            return
        await self._manager.stop(account.id)
        await self._repos.accounts.delete(account.id)
        self._on_state_changed()
        log.info("account '%s' removed by %s", account.name, user_id_of(message))
        await message.answer(f"🗑 Account '{account.name}' removed; monitoring stopped.")
