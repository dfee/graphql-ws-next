from aiohttp import WSMsgType, web

from graphql_ws.abc import AbstractConnectionContext
from graphql_ws.server import ConnectionClosed


class AiohttpConnectionContext(AbstractConnectionContext):
    ws: web.WebSocketResponse  # pylint: disable=C0103, invalid-name

    async def receive(self) -> str:
        message = await self.ws.receive()

        if message.type == WSMsgType.TEXT:
            return message.data

        if message.type in [
            WSMsgType.CLOSED,
            WSMsgType.CLOSING,
            WSMsgType.ERROR,
        ]:
            raise ConnectionClosed

    @property
    def closed(self) -> bool:
        return self.ws.closed

    async def close(self, code: int) -> None:
        await self.ws.close(code=code)

    async def send(self, data: str) -> None:
        if self.closed:
            return
        await self.ws.send_str(data)
