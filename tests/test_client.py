import hashlib
import hmac
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import respx

from binance_monitor.binance.client import (
    BinanceAPIError,
    BinanceClient,
    BinanceRateLimitError,
)
from binance_monitor.binance.user_stream import Backoff, build_stream_url

from .conftest import sample

BASE = "https://fapi.test"


def make(
    http: httpx.AsyncClient, now: float = 1_700_000_000.0, mono: list[float] | None = None
) -> BinanceClient:
    clock_state = mono if mono is not None else [0.0]
    return BinanceClient(
        BASE,
        "APIKEY",
        "SECRET",
        http,
        clock=lambda: now,
        monotonic=lambda: clock_state[0],
    )


@respx.mock
async def test_signed_request_has_valid_hmac_header_and_recv_window() -> None:
    route = respx.get(f"{BASE}/fapi/v1/openOrders").mock(
        return_value=httpx.Response(200, json=sample("open_orders"))
    )
    async with httpx.AsyncClient() as http:
        orders = await make(http).open_orders()
    request = route.calls[0].request
    assert request.headers["X-MBX-APIKEY"] == "APIKEY"
    raw = urlsplit(str(request.url)).query
    query, _, signature = raw.rpartition("&signature=")
    expected = hmac.new(b"SECRET", query.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
    params = dict(parse_qsl(query))
    assert params["recvWindow"] == "5000" and params["timestamp"] == "1700000000000"
    assert orders[0].order_id == 8886774 and str(orders[0].price) == "64250.00"


@respx.mock
async def test_retry_once_on_1021_after_time_sync() -> None:
    sync = respx.get(f"{BASE}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1_700_000_005_000})
    )
    route = respx.get(f"{BASE}/fapi/v1/positionSide/dual").mock(
        side_effect=[
            httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside recvWindow"}),
            httpx.Response(200, json={"dualSidePosition": True}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = make(http)
        assert await client.position_mode_hedge() is True
    assert sync.call_count == 1 and route.call_count == 2
    assert client.time_offset_ms == 5000
    retry_ts = dict(parse_qsl(urlsplit(str(route.calls[1].request.url)).query))["timestamp"]
    assert retry_ts == "1700000005000"


@respx.mock
async def test_1021_is_retried_only_once() -> None:
    respx.get(f"{BASE}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1_700_000_000_000})
    )
    route = respx.get(f"{BASE}/fapi/v3/account").mock(
        return_value=httpx.Response(400, json={"code": -1021, "msg": "ts"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(BinanceAPIError) as exc:
            await make(http).account()
    assert exc.value.code == -1021 and route.call_count == 2


@respx.mock
async def test_api_error_is_raised_with_code_and_msg() -> None:
    respx.get(f"{BASE}/fapi/v3/account").mock(
        return_value=httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(BinanceAPIError) as exc:
            await make(http).account()
    assert exc.value.code == -2015 and "Invalid API-key" in str(exc.value)


@respx.mock
async def test_rate_limit_respects_retry_after_and_blocks_until_expiry() -> None:
    route = respx.get(f"{BASE}/fapi/v1/openOrders").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={})
    )
    mono = [100.0]
    async with httpx.AsyncClient() as http:
        client = make(http, mono=mono)
        with pytest.raises(BinanceRateLimitError) as exc:
            await client.open_orders()
        assert exc.value.retry_after == 30
        calls = route.call_count
        mono[0] = 110.0  # still banned: no network call at all
        with pytest.raises(BinanceRateLimitError):
            await client.open_orders()
        assert route.call_count == calls
        mono[0] = 131.0
        respx.get(f"{BASE}/fapi/v1/openOrders").mock(return_value=httpx.Response(200, json=[]))
        assert await client.open_orders() == []


@respx.mock
async def test_418_ip_ban_defaults_to_long_wait() -> None:
    respx.get(f"{BASE}/fapi/v1/openOrders").mock(return_value=httpx.Response(418, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(BinanceRateLimitError) as exc:
            await make(http).open_orders()
    assert exc.value.status == 418 and exc.value.retry_after == 60


@respx.mock
async def test_positions_algo_orders_and_exchange_info_parsing() -> None:
    respx.get(f"{BASE}/fapi/v3/positionRisk").mock(
        return_value=httpx.Response(200, json=sample("position_risk"))
    )
    respx.get(f"{BASE}/fapi/v1/openAlgoOrders").mock(
        return_value=httpx.Response(200, json=sample("open_algo_orders"))
    )
    respx.get(f"{BASE}/fapi/v1/exchangeInfo").mock(
        return_value=httpx.Response(200, json=sample("exchange_info"))
    )
    async with httpx.AsyncClient() as http:
        client = make(http)
        positions = await client.positions()
        algo = await client.open_algo_orders()
        info = await client.exchange_info()
    assert [p.symbol for p in positions] == ["BTCUSDT"]  # flat ETH row is dropped
    assert str(positions[0].unrealized_pnl) == "0.75"
    assert algo is not None and algo[0].is_algo and algo[0].order_id == 2146760
    assert str(algo[0].stop_price) == "750.000" and algo[0].type == "TAKE_PROFIT"
    assert info["BTCUSDT"].price_precision == 2 and info["BTCUSDT"].base_asset == "BTC"


@respx.mock
async def test_algo_endpoint_404_is_soft() -> None:
    respx.get(f"{BASE}/fapi/v1/openAlgoOrders").mock(return_value=httpx.Response(404, text="nope"))
    async with httpx.AsyncClient() as http:
        assert await make(http).open_algo_orders() is None


@respx.mock
async def test_listen_key_lifecycle_uses_api_key_header_without_signature() -> None:
    post = respx.post(f"{BASE}/fapi/v1/listenKey").mock(
        return_value=httpx.Response(200, json={"listenKey": "abc123"})
    )
    put = respx.put(f"{BASE}/fapi/v1/listenKey").mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(f"{BASE}/fapi/v1/listenKey").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as http:
        client = make(http)
        assert await client.create_listen_key() == "abc123"
        await client.keepalive_listen_key()
        await client.close_listen_key()
    for route in (post, put, delete):
        request = route.calls[0].request
        assert request.headers["X-MBX-APIKEY"] == "APIKEY"
        assert "signature" not in str(request.url)


def test_stream_url_uses_private_path() -> None:
    assert (
        build_stream_url("wss://fstream.binance.com/", "KEY")
        == "wss://fstream.binance.com/private/ws/KEY"
    )


def test_backoff_grows_with_jitter_and_resets() -> None:
    b = Backoff(base=1, cap=10, rng=lambda: 1.0)  # max jitter -> delay itself
    assert [b.next() for _ in range(6)] == [1, 2, 4, 8, 10, 10]
    b.reset()
    assert b.next() == 1
    low = Backoff(base=4, cap=100, rng=lambda: 0.0)
    assert low.next() == 2  # lower bound is half the delay
