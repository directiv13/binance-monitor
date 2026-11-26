import httpx
import respx

from binance_monitor.db.repositories import Repositories
from binance_monitor.notify.pushover import PushoverBroadcaster, PushoverClient, PushoverResult

BASE = "https://pushover.test"
OK = {"status": 1, "request": "abc"}
INVALID = {
    "user": "invalid",
    "errors": ["user identifier is not a valid user, group, or subscribed user key"],
    "status": 0,
}


@respx.mock
async def test_validate_ok_invalid_and_network_error() -> None:
    route = respx.post(f"{BASE}/1/users/validate.json")
    async with httpx.AsyncClient() as http:
        client = PushoverClient(http, "APPTOKEN", BASE)
        route.mock(return_value=httpx.Response(200, json=OK))
        assert await client.validate("good") is PushoverResult.OK
        assert b"token=APPTOKEN" in route.calls[0].request.content
        assert b"user=good" in route.calls[0].request.content
        route.mock(return_value=httpx.Response(400, json=INVALID))
        assert await client.validate("bad") is PushoverResult.INVALID_USER
        route.mock(side_effect=httpx.ConnectError("down"))
        assert await client.validate("x") is PushoverResult.ERROR


@respx.mock
async def test_send_payload() -> None:
    route = respx.post(f"{BASE}/1/messages.json").mock(return_value=httpx.Response(200, json=OK))
    async with httpx.AsyncClient() as http:
        result = await PushoverClient(http, "APPTOKEN", BASE).send("u1", "Title", "Body text")
    assert result is PushoverResult.OK
    body = route.calls[0].request.content.decode()
    assert "token=APPTOKEN" in body and "user=u1" in body
    assert "title=Title" in body and "message=Body+text" in body
    # emergency by default: priority 2 with the mandatory retry/expire
    assert "priority=2" in body and "retry=60" in body and "expire=3600" in body


@respx.mock
async def test_send_priority_is_configurable_and_retry_expire_only_for_emergency() -> None:
    route = respx.post(f"{BASE}/1/messages.json").mock(return_value=httpx.Response(200, json=OK))
    async with httpx.AsyncClient() as http:
        quiet = PushoverClient(http, "T", BASE, priority=-1)
        assert await quiet.send("u1", "Title", "Body") is PushoverResult.OK
        custom = PushoverClient(http, "T", BASE, retry_seconds=30, expire_seconds=600)
        assert await custom.send("u1", "Title", "Body") is PushoverResult.OK
    quiet_body = route.calls[0].request.content.decode()
    assert "priority=-1" in quiet_body and "retry" not in quiet_body and "expire" not in quiet_body
    custom_body = route.calls[1].request.content.decode()
    assert "priority=2" in custom_body and "retry=30" in custom_body and "expire=600" in custom_body


@respx.mock
async def test_broadcast_isolates_failures_and_deactivates_invalid_keys(
    repos: Repositories,
) -> None:
    for uid, key in [(1, "good"), (2, "bad"), (3, "flaky"), (4, "good2")]:
        await repos.subscribers.upsert(uid, key)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "user=bad" in body:
            return httpx.Response(400, json=INVALID)
        if "user=flaky" in body:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=OK)

    route = respx.post(f"{BASE}/1/messages.json").mock(side_effect=handler)
    async with httpx.AsyncClient() as http:
        broadcaster = PushoverBroadcaster(PushoverClient(http, "T", BASE), repos.subscribers)
        await broadcaster.broadcast("Position opened", "BTCUSDT LONG")
    assert route.call_count == 4  # everyone was attempted despite failures
    active = {s.telegram_user_id for s in await repos.subscribers.list_active()}
    assert active == {1, 3, 4}  # only the invalid key was deactivated; flaky stays active
