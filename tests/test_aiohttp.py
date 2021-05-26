import asyncio
import json

from aiohttp import web
import aiohttp.test_utils
import pytest

from graphql_ws.server import SubscriptionServer
from graphql_ws.aiohttp import AiohttpConnectionContext
from graphql_ws.protocol import WS_PROTOCOL, GQLMsgType

from .common import schema

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name


class hotpath:
    def __init__(self, handler):
        self.handler = handler

        self.app = web.Application()
        self.app.router.add_get("/", self.handle_websocket)
        self.server = aiohttp.test_utils.TestServer(self.app)
        self.client = aiohttp.test_utils.TestClient(self.server)

        self.swsr = None  # server websocket response
        self.cwsr = None  # client websocket response

    async def handle_websocket(self, request):
        self.swsr = web.WebSocketResponse(protocols=(WS_PROTOCOL,))
        await self.swsr.prepare(request)
        await self.handler(self.swsr)
        return self.swsr

    async def __aenter__(self):
        await self.client.start_server()
        self.cwsr = await self.client.ws_connect("/")
        return self.cwsr

    async def __aexit__(self, exc_type, exc, tb):
        # pylint: disable=W0613, unused-argument
        await self.client.close()


class TestAiohttpConnectionContext:
    @pytest.mark.asyncio
    async def test_receive(self):
        payload = {"test": 1}

        async def handle(swsr):
            connection_context = AiohttpConnectionContext(swsr)
            assert await connection_context.receive() == json.dumps(payload)

        async with hotpath(handle) as cwsr:
            await cwsr.send_json(payload)
            await cwsr.close()

    @pytest.mark.asyncio
    async def test_close(self):
        async def handle(swsr):
            connection_context = AiohttpConnectionContext(swsr)
            await connection_context.close(1011)

        async with hotpath(handle) as cwsr:
            await cwsr.receive()
            assert cwsr.closed

    @pytest.mark.asyncio
    async def test_closed(self):
        event = asyncio.Event()

        async def handle(swsr):
            connection_context = AiohttpConnectionContext(swsr)
            receive_fut = asyncio.ensure_future(connection_context.receive())
            await event.wait()
            assert connection_context.closed
            receive_fut.cancel()

        async with hotpath(handle) as cwsr:
            event.set()
            await asyncio.sleep(.01)
            await cwsr.close()

    @pytest.mark.asyncio
    async def test_send(self):
        payload = json.dumps({"test": 1})

        async def handle(swsr):
            connection_context = AiohttpConnectionContext(swsr)
            await connection_context.send(payload)

        async with hotpath(handle) as cwsr:
            message = await cwsr.receive()
            assert message.data == payload


class TestIntegration:
    @pytest.fixture
    def server(self):
        return SubscriptionServer(schema, AiohttpConnectionContext)

    @pytest.mark.asyncio
    async def test_query(self, server):
        context = {"name": "Jack"}
        fut = None

        async def handle(swsr):
            nonlocal fut
            fut = asyncio.ensure_future(server.handle(swsr, context))
            await fut

        async with hotpath(handle) as cwsr:
            message = {
                "type": GQLMsgType.START.value,
                "id": "0",
                "payload": {
                    "query": (
                        "query getName($title: String!) { name(title: $title) }"
                    ),
                    "operationName": "getName",
                    "variables": {"title": "Mr."},
                },
            }
            await cwsr.send_json(message)
            resp = await cwsr.receive_json()
            assert resp == {
                "id": "0",
                "type": GQLMsgType.DATA.value,
                "payload": {"data": {"name": "Mr. Jack :: getName"}},
            }

        assert fut.done() and not fut.cancelled() and not fut.exception()

    @pytest.mark.asyncio
    async def test_subscription(self, server):
        context = {"event": asyncio.Event()}
        fut = None
        message = {
            "type": GQLMsgType.START.value,
            "id": "0",
            "payload": {
                "query": (
                    """
                    subscription subscribeCounter ($ceil: Int!) {
                        counter(ceil: $ceil)
                    }
                    """
                ),
                "operationName": "subscribeCounter",
                "variables": {"ceil": 5},
            },
        }

        async def handle(swsr):
            nonlocal fut
            fut = asyncio.ensure_future(server.handle(swsr, context))
            await fut

        async with hotpath(handle) as cwsr:
            await cwsr.send_json(message)
            for i in range(5):
                context["event"].set()
                resp = await cwsr.receive_json()
                assert resp == {
                    "id": "0",
                    "type": GQLMsgType.DATA.value,
                    "payload": {
                        "data": {"counter": f"{i} :: subscribeCounter"}
                    },
                }

            context["event"].set()
            resp = await cwsr.receive_json()
            assert resp == {"id": "0", "type": GQLMsgType.COMPLETE.value}

        assert fut.done() and not fut.cancelled() and not fut.exception()
