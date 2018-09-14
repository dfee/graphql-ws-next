import typing

from aiohttp import WSMsgType, web
import graphql

from graphql_ws.abstract import AbstractSubscriptionServer


class AiohttpSubscriptionServer(AbstractSubscriptionServer):
    ws: web.WebSocketResponse  # pylint: disable=C0103, invalid-name

    def __init__(self, ws, schema, context_value):
        self.ws = ws  # pylint: disable=C0103, invalid-name
        super().__init__(schema, context_value)

    async def __call__(self) -> None:
        await self.on_open()
        async for message in self.ws:
            if message.type == WSMsgType.TEXT:
                await self.on_message(message.data)
            elif message.type == WSMsgType.ERROR:
                break
        await self.on_close()

    @property
    def closed(self) -> bool:
        return self.ws.closed

    async def close(self, code: int) -> None:
        await self.ws.close(code=code)

    async def send(self, data: typing.Dict[str, typing.Any]) -> None:
        if self.closed:
            return
        await self.ws.send_json(data)


async def subscribe(
    ws: web.WebSocketResponse,
    schema: graphql.GraphQLSchema,
    context_value: typing.Any = None,
) -> web.WebSocketResponse:
    # pylint: disable=C0103, invalid-name
    server = AiohttpSubscriptionServer(ws, schema, context_value)
    await server()
    return ws
