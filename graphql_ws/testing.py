import asyncio
import json
import typing

from graphql_ws.abc import AbstractConnectionContext
from graphql_ws.server import ConnectionClosed

CLOSED = "__CLOSED__"


class TestWebsocket:
    transport: "TestWebsocketTransport"
    local = asyncio.Queue()
    remote = asyncio.Queue()

    def __init__(self, transport, local, remote):
        self.transport = transport
        self.local = local
        self.remote = remote

    async def receive(self) -> str:
        return await self.local.get()

    async def receive_json(self) -> typing.Dict[str, typing.Any]:
        return json.loads(await self.receive())

    async def send(self, message: str) -> None:
        await self.remote.put(message)

    async def send_json(self, message: typing.Any) -> None:
        await self.send(json.dumps(message))

    @property
    def closed(self) -> bool:
        return self.transport.closed

    async def close(self, code: int) -> None:
        await self.transport.close(code)


class TestWebsocketTransport:
    client: TestWebsocket
    server: TestWebsocket
    _close_event: asyncio.Event
    close_code: typing.Optional[int] = None

    def __init__(self):
        queue1, queue2 = asyncio.Queue(), asyncio.Queue()
        self.client = TestWebsocket(self, queue1, queue2)
        self.server = TestWebsocket(self, queue2, queue1)
        self._close_event = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._close_event.is_set()

    async def close(self, code: int) -> None:
        self._close_event.set()
        await self.client.local.put(CLOSED)
        await self.server.local.put(CLOSED)
        self.close_code = code


class TestConnectionContext(AbstractConnectionContext):
    ws: TestWebsocket

    def __init__(self, ws, context_value):
        super().__init__(ws, context_value)
        self.ws = ws

    async def receive(self) -> str:
        message = await self.ws.receive()
        if message == CLOSED:
            raise ConnectionClosed()
        return message

    @property
    def closed(self) -> bool:
        return self.ws.closed

    async def close(self, code: int) -> None:
        await self.ws.close(code)

    async def send(self, data: str) -> None:
        if self.closed:
            return
        await self.ws.send(data)
