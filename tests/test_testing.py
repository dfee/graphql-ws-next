import asyncio
import json
from unittest.mock import Mock

import pytest

from graphql_ws.protocol import GQLMsgType
from graphql_ws.server import ConnectionClosed, SubscriptionServer
from graphql_ws.testing import (
    CLOSED,
    TestConnectionContext as _TestConnectionContext,
    TestWebsocket as _TestWebsocket,
    TestWebsocketTransport as _TestWebsocketTransport,
)

from .common import AsyncMock, schema

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name


class TestTestWebsocket:
    @pytest.fixture
    def endpoint(self):
        return _TestWebsocket(AsyncMock(), asyncio.Queue(), asyncio.Queue())

    @pytest.mark.asyncio
    async def test_receive(self, endpoint):
        message = "test"
        endpoint.local.put_nowait(message)
        assert await endpoint.receive() == message

    @pytest.mark.asyncio
    async def test_receive_json(self, endpoint):
        message = {"test": True}
        endpoint.local.put_nowait(json.dumps(message))
        assert await endpoint.receive_json() == message

    @pytest.mark.asyncio
    async def test_send(self, endpoint):
        message = "test"
        await endpoint.send(message)
        assert endpoint.remote.get_nowait() == message

    @pytest.mark.asyncio
    async def test_send_json(self, endpoint):
        message = {"test": True}
        await endpoint.send_json(message)
        assert endpoint.remote.get_nowait() == json.dumps(message)

    @pytest.mark.asyncio
    async def test_close(self, endpoint):
        endpoint.transport.close = AsyncMock()
        await endpoint.close(1011)
        endpoint.transport.close.assert_called_once_with(1011)

    def test_closed(self, endpoint):
        endpoint.transport.close_code = 1011
        assert endpoint.closed


class TestTestWebsocketTransport:
    @pytest.fixture
    def transport(self):
        return _TestWebsocketTransport()

    @pytest.mark.asyncio
    async def test_routing(self, transport):
        message = "test"

        await transport.client.send(message)
        server_received = await transport.server.receive()

        await transport.server.send(message)
        client_received = await transport.client.receive()

        assert client_received == server_received == message

    @pytest.mark.asyncio
    async def test_close(self, transport):
        await transport.close(1011)
        assert (
            transport.client.local.get_nowait()
            == transport.server.local.get_nowait()
            == CLOSED
        )
        assert transport.closed


class TestTestConnectionContext:
    @pytest.fixture
    async def transport(self):
        return _TestWebsocketTransport()

    @pytest.fixture
    def client_ws(self, transport):
        return transport.client

    @pytest.fixture
    def connection_context(self, transport):
        return _TestConnectionContext(transport.server, Mock())

    @pytest.mark.asyncio
    async def test_receive(self, transport, connection_context):
        payload = {"test": 1}
        await transport.client.send_json(payload)
        assert await connection_context.receive() == json.dumps(payload)

    @pytest.mark.asyncio
    async def test_closed(self, transport, connection_context):
        assert not connection_context.closed
        await transport.close(1011)
        assert connection_context.closed

    @pytest.mark.asyncio
    async def test_close(self, connection_context):
        fut = asyncio.ensure_future(connection_context.receive())
        await connection_context.close(1011)
        await asyncio.sleep(.01)
        assert connection_context.closed
        assert fut.exception() and isinstance(fut.exception(), ConnectionClosed)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("closed", (False, True))
    async def test_send(self, transport, client_ws, connection_context, closed):
        if closed:
            transport._close_event.set()

        await connection_context.send(json.dumps({"test": 0}))

        if closed:
            assert not client_ws.local.qsize()
        else:
            assert await client_ws.receive_json() == {"test": 0}


class TestIntegration:
    @pytest.fixture
    async def transport(self):
        return _TestWebsocketTransport()

    @pytest.fixture
    def server(self):
        return SubscriptionServer(schema, _TestConnectionContext)

    @pytest.mark.asyncio
    async def test_query(self, transport, server):
        context = {"name": "Jack"}

        async def handle(ws):
            await server.handle(ws, context)

        fut = asyncio.ensure_future(handle(transport.server))
        await asyncio.sleep(.01)

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

        await transport.client.send_json(message)
        resp = await transport.client.receive_json()
        assert resp == {
            "id": "0",
            "type": GQLMsgType.DATA.value,
            "payload": {"data": {"name": "Mr. Jack :: getName"}},
        }

        await transport.client.send(CLOSED)
        await asyncio.sleep(.01)

        assert fut.done() and not fut.cancelled() and not fut.exception()

    @pytest.mark.asyncio
    async def test_subscription(self, transport, server):
        context = {"event": asyncio.Event()}

        async def handle(ws):
            await server.handle(ws, context)

        fut = asyncio.ensure_future(handle(transport.server))
        await asyncio.sleep(.01)

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

        await transport.client.send_json(message)

        for i in range(5):
            context["event"].set()
            resp = await transport.client.receive_json()
            assert resp == {
                "id": "0",
                "type": GQLMsgType.DATA.value,
                "payload": {"data": {"counter": f"{i} :: subscribeCounter"}},
            }

        context["event"].set()
        resp = await transport.client.receive_json()
        assert resp == {"id": "0", "type": GQLMsgType.COMPLETE.value}

        # operation is closed, now close the connection
        await transport.client.send(CLOSED)
        await asyncio.sleep(.01)
        assert fut.done() and not fut.cancelled() and not fut.exception()
