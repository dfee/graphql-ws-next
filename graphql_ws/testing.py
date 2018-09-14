import asyncio
import json
import typing

from graphql_ws.abstract import AbstractSubscriptionServer

TERMINATE = '"__TERMINATE__"'
_TERMINATE = "__TERMINATE__"


class TestWebsocket:
    connection: "TestWebsocketTransport"
    local = asyncio.Queue()
    remote = asyncio.Queue()

    def __init__(self, connection, local, remote):
        self.connection = connection
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
        return self.connection.closed

    def close(self, code: int) -> None:
        self.connection.close(code)


class TestWebsocketTransport:
    client: TestWebsocket
    server: TestWebsocket
    close_code: typing.Optional[int]

    def __init__(self):
        queue1, queue2 = asyncio.Queue(), asyncio.Queue()
        self.client = TestWebsocket(self, queue1, queue2)
        self.server = TestWebsocket(self, queue2, queue1)
        self.close_code = None

    @property
    def closed(self) -> bool:
        return self.close_code is not None

    def close(self, code: int) -> None:
        self.close_code = code


class TestSubscriptionServer(AbstractSubscriptionServer):
    ws: TestWebsocket

    def __init__(self, ws, schema, context_value):
        super().__init__(schema, context_value)
        self.ws = ws

    async def __call__(self) -> None:
        await self.on_open()
        while True:
            message = await self.ws.receive_json()
            if message == _TERMINATE:
                break
            await self.on_message(message)
        await self.on_close()

    @property
    def closed(self) -> bool:
        return self.ws.closed

    async def close(self, code: int) -> None:
        self.ws.close(code)

    async def send(self, data: typing.Dict[str, typing.Any]) -> None:
        if self.closed:
            return
        await self.ws.send_json(data)
