import json

from websockets.asyncio.server import ServerConnection, serve

from binance_monitor.binance.user_stream import websocket_connector

from .conftest import order_event


async def test_real_websocket_connector_yields_frames_and_stops_on_close() -> None:
    seen_paths: list[str] = []

    async def handler(ws: ServerConnection) -> None:
        assert ws.request is not None
        seen_paths.append(ws.request.path)
        await ws.send(json.dumps(order_event()))
        await ws.send(json.dumps({"e": "listenKeyExpired", "E": 1}))
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        frames: list[dict[str, object]] = []
        async with websocket_connector(f"ws://127.0.0.1:{port}/private/ws/KEY") as stream:
            async for raw in stream:
                frames.append(json.loads(raw))

    assert seen_paths == ["/private/ws/KEY"]
    assert [f["e"] for f in frames] == ["ORDER_TRADE_UPDATE", "listenKeyExpired"]
