from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User
from cryptography.fernet import Fernet

from binance_monitor.binance.client import BinanceAPIError
from binance_monitor.binance.models import Account
from binance_monitor.bot.admin import NOT_ALLOWED, AdminHandlers
from binance_monitor.bot.router import build_router
from binance_monitor.bot.user import UserHandlers
from binance_monitor.crypto import Crypto
from binance_monitor.db.repositories import Repositories
from binance_monitor.notify.pushover import PushoverResult

ADMIN, USER = 1, 2
KEY, SECRET = "AAAAkeykeykeykeyZZZZ", "BBBBsecretsecretYYYY"


class FakeManager:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.started: list[Account] = []
        self.stopped: list[int] = []
        self.validated: list[tuple[str, str]] = []

    async def validate_credentials(self, api_key: str, api_secret: str) -> None:
        self.validated.append((api_key, api_secret))
        if self.error:
            raise self.error

    async def start(self, account: Account) -> None:
        self.started.append(account)

    async def stop(self, account_id: int) -> None:
        self.stopped.append(account_id)


class FakePushover:
    def __init__(self, result: PushoverResult = PushoverResult.OK) -> None:
        self.result = result
        self.keys: list[str] = []

    async def validate(self, user_key: str) -> PushoverResult:
        self.keys.append(user_key)
        return self.result


def msg(text: str, user_id: int = ADMIN, chat_type: str = "private") -> MagicMock:
    m = MagicMock()
    m.text = text
    m.from_user.id = user_id
    m.chat.type = chat_type
    m.chat.id = 5
    m.answer = AsyncMock()
    m.reply = AsyncMock()
    m.delete = AsyncMock()
    return m


def replies(m: MagicMock) -> str:
    return " | ".join(str(c.args[0]) for c in m.answer.call_args_list)


class Env:
    def __init__(self, repos: Repositories) -> None:
        self.repos = repos
        self.crypto = Crypto(Fernet.generate_key().decode())
        self.manager = FakeManager()
        self.pushover = FakePushover()
        self.state_changed = MagicMock()
        self.admin = AdminHandlers(
            admin_ids={ADMIN},
            repos=repos,
            crypto=self.crypto,
            manager=self.manager,
            on_state_changed=self.state_changed,
        )
        self.user = UserHandlers(subscribers=repos.subscribers, pushover=self.pushover)


@pytest.fixture
def env(repos: Repositories) -> Env:
    return Env(repos)


# ------------------------------------------------------------------ /add_account


async def test_add_account_happy_path(env: Env) -> None:
    m = msg(f"/add_account main-acc {KEY} {SECRET}")
    await env.admin.add_account(m)
    m.delete.assert_awaited_once()  # secrets message deleted
    assert env.manager.validated == [(KEY, SECRET)]
    account = await env.repos.accounts.get_by_name("main-acc")
    assert account is not None and account.created_by == ADMIN
    assert KEY not in account.api_key_enc and SECRET not in account.api_secret_enc  # encrypted
    assert env.crypto.decrypt(account.api_key_enc) == KEY
    assert env.crypto.decrypt(account.api_secret_enc) == SECRET
    assert env.manager.started == [account]  # monitoring starts right away
    reply = replies(m)
    assert "main-acc" in reply and KEY not in reply and SECRET not in reply
    env.state_changed.assert_called()


async def test_add_account_dash_form_is_parsed_the_same(env: Env) -> None:
    m = msg(f"/add-account dash {KEY} {SECRET}")
    await env.admin.add_account(m)
    assert await env.repos.accounts.get_by_name("dash") is not None


async def test_message_deleted_even_when_validation_fails(env: Env) -> None:
    env.manager.error = BinanceAPIError(-2015, "Invalid API-key, IP, or permissions")
    m = msg(f"/add_account main {KEY} {SECRET}")
    await env.admin.add_account(m)
    m.delete.assert_awaited_once()
    assert "-2015" in replies(m) and "Invalid API-key" in replies(m)
    assert KEY not in replies(m) and SECRET not in replies(m)
    assert await env.repos.accounts.list_all() == []  # nothing stored
    assert env.manager.started == []


async def test_non_admin_is_denied_but_message_is_still_deleted(env: Env) -> None:
    m = msg(f"/add_account main {KEY} {SECRET}", user_id=USER)
    await env.admin.add_account(m)
    m.delete.assert_awaited_once()
    assert replies(m) == NOT_ALLOWED
    assert env.manager.validated == [] and await env.repos.accounts.list_all() == []


async def test_network_failure_stores_nothing(env: Env) -> None:
    env.manager.error = ConnectionError("down")
    m = msg(f"/add_account main {KEY} {SECRET}")
    await env.admin.add_account(m)
    assert "Could not reach Binance" in replies(m) and await env.repos.accounts.list_all() == []


@pytest.mark.parametrize("name", ["bad name!", "x" * 33, "ünï"])
async def test_invalid_names_rejected(env: Env, name: str) -> None:
    m = msg(f"/add_account {name.replace(' ', '')} {KEY} {SECRET}")
    await env.admin.add_account(m)
    assert "Invalid name" in replies(m) and env.manager.validated == []


async def test_wrong_arg_count_and_duplicate_name(env: Env) -> None:
    m = msg("/add_account onlyname")
    await env.admin.add_account(m)
    m.delete.assert_awaited_once()
    assert "Usage" in replies(m)

    await env.admin.add_account(msg(f"/add_account dup {KEY} {SECRET}"))
    m2 = msg(f"/add_account dup {KEY} {SECRET}")
    await env.admin.add_account(m2)
    assert "already exists" in replies(m2)
    assert len(env.manager.validated) == 1  # duplicate rejected before any Binance call


async def test_delete_failure_does_not_break_flow(env: Env) -> None:
    m = msg(f"/add_account main {KEY} {SECRET}")
    m.delete.side_effect = TelegramBadRequest(method=MagicMock(), message="can't delete")
    await env.admin.add_account(m)
    assert await env.repos.accounts.get_by_name("main") is not None


# ------------------------------------------------------------------ /accounts, /remove_account


async def test_accounts_list_and_remove(env: Env) -> None:
    m0 = msg("/accounts")
    await env.admin.accounts(m0)
    assert "No accounts" in replies(m0)

    a = await env.repos.accounts.create("alpha", "k", "s", ADMIN)
    b = await env.repos.accounts.create("beta", "k", "s", ADMIN)
    await env.repos.accounts.set_status(a.id, "connected")
    await env.repos.accounts.set_status(b.id, "error", "IP banned")
    m1 = msg("/accounts")
    await env.admin.accounts(m1)
    text = replies(m1)
    assert "alpha — connected" in text and "beta — error" in text and "IP banned" in text
    assert "added" in text

    m2 = msg("/remove_account alpha")
    await env.admin.remove_account(m2)
    assert env.manager.stopped == [a.id]
    assert await env.repos.accounts.get_by_name("alpha") is None
    m3 = msg("/remove_account nope")
    await env.admin.remove_account(m3)
    assert "No account named" in replies(m3)


async def test_admin_only_commands_deny_others(env: Env) -> None:
    for handler, text in [
        (env.admin.accounts, "/accounts"),
        (env.admin.remove_account, "/remove_account x"),
    ]:
        m = msg(text, user_id=USER)
        await handler(m)
        assert replies(m) == NOT_ALLOWED


# ------------------------------------------------------------------ /subscribe, /unsubscribe


async def test_subscribe_validates_then_upserts_and_resubscribe_replaces(env: Env) -> None:
    m = msg("/subscribe ukey1", user_id=USER)
    await env.user.subscribe(m)
    assert env.pushover.keys == ["ukey1"] and "Subscribed" in replies(m)
    await env.user.subscribe(msg("/subscribe ukey2", user_id=USER))
    subs = await env.repos.subscribers.list_active()
    assert [(s.telegram_user_id, s.pushover_key) for s in subs] == [(USER, "ukey2")]


async def test_subscribe_invalid_key_or_pushover_down_saves_nothing(env: Env) -> None:
    env.pushover.result = PushoverResult.INVALID_USER
    m = msg("/subscribe bad", user_id=USER)
    await env.user.subscribe(m)
    assert "not valid" in replies(m)
    env.pushover.result = PushoverResult.ERROR
    m2 = msg("/subscribe whatever", user_id=USER)
    await env.user.subscribe(m2)
    assert "Try again later" in replies(m2)
    assert await env.repos.subscribers.list_active() == []
    m3 = msg("/subscribe", user_id=USER)
    await env.user.subscribe(m3)
    assert "Usage" in replies(m3)


async def test_unsubscribe_is_idempotent(env: Env) -> None:
    await env.repos.subscribers.upsert(USER, "k")
    m = msg("/unsubscribe", user_id=USER)
    await env.user.unsubscribe(m)
    assert "Unsubscribed" in replies(m) and await env.repos.subscribers.list_active() == []
    m2 = msg("/unsubscribe", user_id=USER)
    await env.user.unsubscribe(m2)  # none existed: still fine
    assert "Unsubscribed" in replies(m2)


async def test_start_and_help_text(env: Env) -> None:
    m = msg("/start")
    await env.user.start(m)
    assert "/subscribe" in replies(m) and "/unsubscribe" in replies(m)


# ------------------------------------------------------------------ real aiogram routing


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture
def routed(env: Env, monkeypatch: pytest.MonkeyPatch) -> tuple[Dispatcher, Bot, Recorder]:
    recorder = Recorder()

    async def fake_call(
        self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None
    ) -> Any:
        recorder.calls.append((type(method).__name__, method.model_dump()))
        if type(method).__name__ == "SendMessage":
            return Message(
                message_id=99, date=datetime.now(UTC), chat=Chat(id=5, type="private"), text="ok"
            )
        return True

    monkeypatch.setattr(Bot, "__call__", fake_call)
    dp = Dispatcher()
    dp.include_router(build_router(env.admin, env.user))
    return dp, Bot(token="123456:TESTTOKEN"), recorder


async def feed(dp: Dispatcher, bot: Bot, text: str, *, user_id: int, chat_type: str) -> None:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=5 if chat_type == "private" else -100123, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
        text=text,
    )
    await dp.feed_update(bot, Update(update_id=1, message=message))


async def test_routing_private_dash_command(env: Env, routed: Any) -> None:
    dp, bot, rec = routed
    await feed(dp, bot, f"/add-account viaRoute {KEY} {SECRET}", user_id=ADMIN, chat_type="private")
    assert "DeleteMessage" in rec.names() and "SendMessage" in rec.names()
    assert await env.repos.accounts.get_by_name("viaRoute") is not None


async def test_routing_private_underscore_command_and_non_admin(env: Env, routed: Any) -> None:
    dp, bot, rec = routed
    await feed(dp, bot, "/accounts", user_id=USER, chat_type="private")
    sent = [d["text"] for n, d in rec.calls if n == "SendMessage"]
    assert sent == [NOT_ALLOWED]


async def test_group_private_only_commands_get_dm_me_and_secrets_deleted(
    env: Env, routed: Any
) -> None:
    dp, bot, rec = routed
    await feed(dp, bot, "/subscribe abc", user_id=USER, chat_type="supergroup")
    assert [d["text"] for n, d in rec.calls if n == "SendMessage"] == [
        "Please DM me for this command."
    ]
    assert "DeleteMessage" not in rec.names()

    rec.calls.clear()
    await feed(dp, bot, f"/add_account x {KEY} {SECRET}", user_id=ADMIN, chat_type="supergroup")
    assert "DeleteMessage" in rec.names()  # secrets pasted into a group get removed
    assert await env.repos.accounts.get_by_name("x") is None  # and are NOT processed


async def test_group_ignores_everything_else(env: Env, routed: Any) -> None:
    dp, bot, rec = routed
    for text in ["hello", "/start", "/help", "/unknowncmd"]:
        await feed(dp, bot, text, user_id=USER, chat_type="supergroup")
    assert rec.calls == []
