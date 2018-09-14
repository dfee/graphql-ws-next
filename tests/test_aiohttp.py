import asyncio
import typing

from aiohttp import WSMessage, WSMsgType, web
import pytest

from graphql_ws.aiohttp import AiohttpSubscriptionServer, subscribe
from graphql_ws.protocol import WS_INTERNAL_ERROR

from .common import AsyncMock

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name


class TestAiohttpSubscriptionServer:
    @pytest.fixture
    def subserver(self, wsr, schema, context_value):
        return AiohttpSubscriptionServer(wsr, schema, context_value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("err_closing",), [(True,), (False,)])
    async def test__call__(self, subserver, err_closing):
        subserver.on_open = AsyncMock()
        subserver.on_message = AsyncMock()
        subserver.on_close = AsyncMock()

        txt_event = asyncio.Event()
        txt_msg = WSMessage(WSMsgType.TEXT, "message", "")
        err_event = asyncio.Event()
        err_msg = WSMessage(WSMsgType.ERROR, "", "")

        async def wsr_as_aiter():
            await txt_event.wait()
            yield txt_msg
            if err_closing:
                await err_event.wait()
                yield err_msg
                raise Exception()

        subserver.ws = wsr_as_aiter()
        fut = asyncio.ensure_future(subserver())
        await asyncio.sleep(.01)

        # ensure on_open was called
        assert not fut.done()
        subserver.on_open.assert_called_once_with()

        # ensure on_open was called
        txt_event.set()
        await asyncio.sleep(.01)
        subserver.on_message.assert_called_once_with(txt_msg.data)

        if err_closing:
            # ensure error breaks the loop was called
            assert not fut.done()
            err_event.set()
            await asyncio.sleep(.01)

        assert fut.done()
        assert not fut.exception()
        subserver.on_close.assert_called_once_with()

    def test_closed(self, wsr, subserver):
        wsr.closed = True
        assert subserver.closed

    @pytest.mark.asyncio
    async def test_close(self, wsr, subserver):
        wsr.close = AsyncMock()
        await subserver.close(code=WS_INTERNAL_ERROR)
        wsr.close.assert_called_once_with(code=WS_INTERNAL_ERROR)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("closed", (False, True))
    async def test_send(self, closed, subserver, wsr):
        wsr.send_json = AsyncMock()
        wsr.closed = closed
        data = object()
        await subserver.send(data)
        if closed:
            wsr.send_json.assert_not_called()
        else:
            wsr.send_json.assert_called_once_with(data)


@pytest.mark.asyncio
async def test_subscribe(wsr, schema, context_value, monkeypatch):
    called_with: typing.Optional[
        None,
        typing.Tuple[
            AiohttpSubscriptionServer,  # self
            typing.Tuple[...],  # args
            typing.Dict[str, typing.Any],  # kwargs
        ],
    ] = None

    async def mock__call__(self, *args, **kwargs):
        nonlocal called_with
        called_with = (self, args, kwargs)

    monkeypatch.setattr(AiohttpSubscriptionServer, "__call__", mock__call__)
    resp = await subscribe(wsr, schema, context_value)

    assert called_with is not None
    # pylint: disable=E1136, unsubscriptable-object
    subserver = called_with[0]
    assert isinstance(subserver, AiohttpSubscriptionServer)
    assert not called_with[1]
    assert not called_with[2]

    assert subserver.schema is schema
    assert subserver.context_value is context_value

    assert resp is wsr


# Integration tests follow


@pytest.fixture
def context_value():
    return {
        "first_name": "Jack",
        "last_name": "Black",
        "queue": asyncio.Queue(),
    }


@pytest.fixture
def schema():
    from .common import schema

    return schema


@pytest.fixture
def app(schema, context_value):
    async def handle_websocket(request):
        ws = web.WebSocketResponse(protocols=["graphql-ws"])
        await ws.prepare(request)
        subserver = AiohttpSubscriptionServer(ws, schema, context_value)
        context_value["subserver"] = subserver
        return await subserver()

    app = web.Application()
    app.add_routes([web.get("/", handle_websocket)])
    return app


@pytest.fixture
async def client(event_loop, app):
    from aiohttp.test_utils import TestServer, TestClient

    _server = TestServer(app, loop=event_loop)
    _client = TestClient(_server, loop=event_loop)
    await _client.start_server()
    yield _client
    await _client.close()
