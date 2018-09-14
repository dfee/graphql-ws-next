import asyncio
import json
from unittest.mock import Mock

import pytest

from graphql_ws.testing import (
    TERMINATE,
    TestSubscriptionServer as _TestSubscriptionServer,
    TestWebsocket as _TestWebsocket,
    TestWebsocketTransport as _TestWebsocketTransport,
)

from .common import AsyncMock

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name


class TestTestSubscriptionServer:
    @pytest.fixture
    async def transport(self):
        return _TestWebsocketTransport()

    @pytest.fixture
    def client(self, transport):
        return transport.client

    @pytest.fixture
    def server(self, transport):
        return _TestSubscriptionServer(transport.server, Mock(), Mock())

    @pytest.mark.asyncio
    async def test__call__(self, client, server):
        server.on_open = AsyncMock()
        server.on_message = AsyncMock()
        server.on_close = AsyncMock()

        fut = asyncio.ensure_future(server())
        await asyncio.sleep(.01)
        server.on_open.assert_called_once_with()

        for i in range(5):
            await client.send(f'{{"test": {i}}}')
            await asyncio.sleep(.01)
            server.on_message.assert_called_once_with({"test": i})
            server.on_message.reset_mock()

        await client.send(TERMINATE)
        await asyncio.sleep(.01)
        server.on_close.assert_called_once_with()
        assert fut.done() and not fut.exception()

    @pytest.mark.asyncio
    async def test_closed(self, transport, server):
        assert not server.closed
        transport.close(1011)
        assert server.closed

    @pytest.mark.asyncio
    async def test_close(self, transport, server):
        await server.close(1011)
        assert transport.close_code == 1011

    @pytest.mark.asyncio
    @pytest.mark.parametrize("closed", (True, False))
    async def test_send(self, transport, client, server, closed):
        if closed:
            transport.close_code = 1011

        await server.send({"test": 0})

        if closed:
            assert not client.local.qsize()
        else:
            assert await client.receive_json() == {"test": 0}


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

    def test_close(self, endpoint):
        endpoint.connection.close = Mock()
        endpoint.close(1011)
        endpoint.connection.close.assert_called_once_with(1011)

    def test_closed(self, endpoint):
        endpoint.connection.close_code = 1011
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

    def test_close(self, transport):
        transport.close(1011)
        assert transport.closed
