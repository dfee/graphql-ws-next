import asyncio
import gc
import json
from unittest.mock import Mock

import graphql
import pytest

from graphql_ws.abc import AbstractConnectionContext
from graphql_ws.protocol import WS_INTERNAL_ERROR, GQLMsgType
from graphql_ws.server import (
    SubscriptionServer,
    ConnectionClosed,
    close_cancelling,
)

from .common import AsyncMock, schema

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name

OP_ID = "identity"

ERROR = graphql.GraphQLError(
    "test message",
    graphql.language.Node(),
    source=graphql.Source("query { something }"),
    positions=[14, 40],
    path=["one", 2],
    original_error=ValueError("original"),
    extensions={"ext": None},
)

FORMATTED_ERROR = {
    "message": "test message",
    "locations": [graphql.SourceLocation(1, 15), graphql.SourceLocation(1, 20)],
    "path": ERROR.path,
    "extensions": ERROR.extensions,
}


class TestCloseCancelling:
    # pylint: disable=E1133, not-an-iterable

    @pytest.mark.asyncio
    async def test_aclose(self):
        agen_finalized = False
        completed = False
        received = []
        queue = asyncio.Queue()
        wrapped = None

        async def agen():
            try:
                while True:
                    yield await queue.get()
            finally:
                nonlocal agen_finalized
                agen_finalized = True

        async def run():
            nonlocal wrapped
            wrapped = close_cancelling(agen())
            try:
                async for v in wrapped:
                    received.append(v)
            except asyncio.CancelledError:
                nonlocal completed
                completed = True

        task = asyncio.ensure_future(run())
        for i in range(5):
            await queue.put(i)
            await asyncio.sleep(.01)
        await wrapped.aclose()
        await asyncio.sleep(.01)

        assert received == list(range(5))
        assert agen_finalized
        assert task.done()

    @pytest.mark.asyncio
    async def test_complete(self):
        agen_finalized = False
        completed = False
        received = []
        wrapped = None

        async def agen():
            try:
                yield 1
            finally:
                nonlocal agen_finalized
                agen_finalized = True

        async def run():
            nonlocal wrapped
            wrapped = close_cancelling(agen())

            async for v in wrapped:
                received.append(v)

            nonlocal completed
            completed = True

        task = asyncio.ensure_future(run())
        await asyncio.sleep(.01)

        assert received == [1]
        assert agen_finalized
        assert completed
        assert task.done()


class DummyConnectionContext(AbstractConnectionContext):
    CLOSED = "CLOSED"
    close_code = None
    registry = []

    def __init__(self, ws, context_value):
        super().__init__(ws, context_value)
        self.registry.append(self)
        self.receive_queue = asyncio.Queue()
        self.send_queue = asyncio.Queue()

    @property
    def closed(self):
        return bool(self.close_code)

    async def close(self, code):
        self.close_code = code

    async def receive(self):
        message = await self.receive_queue.get()
        if message == self.CLOSED:
            raise ConnectionClosed
        return message

    async def send(self, message):
        await self.send_queue.put(message)


class TestSubscriptionServer:
    @pytest.fixture
    def server(self, event_loop):
        # pylint: disable=W0613, unused-argument
        DummyConnectionContext.registry.clear()
        return SubscriptionServer(schema, DummyConnectionContext)

    @pytest.fixture
    def conn_context(self):
        ws, context_value = AsyncMock(), Mock()
        return DummyConnectionContext(ws, context_value)

    @pytest.mark.asyncio
    async def test_handle_shield(self, server):
        ws, context_value = AsyncMock(), Mock()
        _handle = server._handle
        _handle_task = None

        def se_handle(*args, **kwargs):
            nonlocal _handle_task
            _handle_task = asyncio.ensure_future(_handle(*args, **kwargs))
            return _handle_task

        server._handle = Mock(side_effect=se_handle)
        task = asyncio.ensure_future(server.handle(ws, context_value))
        await asyncio.sleep(.01)

        task.cancel()
        await asyncio.sleep(.01)
        assert not _handle_task.cancelled()

        # cleanup
        _handle_task.cancel()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("has_operations", (False, True))
    async def test__handle(self, server, conn_context, has_operations):
        server.on_close = Mock(side_effect=server.on_close)
        server.on_open = Mock(side_effect=server.on_open)

        if has_operations:
            mock_op = AsyncMock()
            conn_context[OP_ID] = mock_op

        task = asyncio.ensure_future(server._handle(conn_context))
        await asyncio.sleep(.01)

        server.on_open.assert_called_once_with(conn_context)

        conn_context.receive_queue.put_nowait(DummyConnectionContext.CLOSED)
        await asyncio.sleep(.01)
        server.on_close.assert_called_once_with(conn_context)

        if has_operations:
            mock_op.aclose.assert_called_once_with()
        else:
            assert not conn_context

        assert task.done() and not task.exception()

    @pytest.mark.asyncio
    async def test_send_message(self, server, conn_context):
        await server.send_message(
            conn_context, OP_ID, GQLMsgType.DATA, {"test": "data"}
        )
        assert conn_context.send_queue.qsize() == 1

        msg = conn_context.send_queue.get_nowait()
        assert json.loads(msg) == {
            "type": GQLMsgType.DATA.value,
            "id": OP_ID,
            "payload": {"test": "data"},
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_type", [GQLMsgType.ERROR, GQLMsgType.CONNECTION_ERROR, None]
    )
    async def test_send_error(self, server, conn_context, error_type):
        await server.send_error(
            conn_context, OP_ID, Exception("test"), error_type
        )
        assert conn_context.send_queue.qsize() == 1

        msg = conn_context.send_queue.get_nowait()
        assert json.loads(msg) == {
            "type": (error_type or GQLMsgType.ERROR).value,
            "id": OP_ID,
            "payload": {"message": "test"},
        }

    @pytest.mark.asyncio
    async def test_send_error_invalid_type_raises(self, server, conn_context):
        with pytest.raises(AssertionError) as exctype:
            await server.send_error(
                conn_context, OP_ID, Exception("test"), GQLMsgType.DATA
            )
        assert exctype.value.args[0] == (
            "error_type should be one of the allowed error messages "
            "GQLMessageType.CONNECTION_ERROR or GQLMsgType.ERROR"
        )

    @pytest.mark.asyncio
    async def test_send_execution_result(self, server, conn_context):
        data = {"test": "data"}
        error = graphql.GraphQLError(
            "test message",
            graphql.language.Node(),
            source=graphql.Source("query { something }"),
            positions=[14, 40],
            path=["one", 2],
            original_error=ValueError("original"),
            extensions={"ext": None},
        )
        exec_result = graphql.ExecutionResult(data=data, errors=[error])

        await server.send_execution_result(conn_context, OP_ID, exec_result)
        assert conn_context.send_queue.qsize() == 1

        msg = conn_context.send_queue.get_nowait()
        assert json.loads(msg) == {
            "type": GQLMsgType.DATA.value,
            "id": OP_ID,
            "payload": {
                "data": data,
                "errors": [
                    {
                        "message": "test message",
                        "locations": [
                            {"line": 1, "column": 15}, 
                            {"line": 1, "column": 20}],
                        "path": ERROR.path,
                        "extensions": ERROR.extensions,
                    }
                ],
            },
        }

    @pytest.mark.asyncio
    async def test_unsubscribe(self, server, conn_context):
        server.on_operation_complete = AsyncMock()
        agen_mock = AsyncMock()
        conn_context[OP_ID] = agen_mock

        await server.unsubscribe(conn_context, OP_ID)

        server.on_operation_complete.assert_called_once_with(
            conn_context, OP_ID
        )
        agen_mock.aclose.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_on_close(self, server, conn_context):
        server.unsubscribe = AsyncMock()
        agen_mock = AsyncMock()

        conn_context[OP_ID] = agen_mock
        await server.on_close(conn_context)
        server.unsubscribe.assert_called_once_with(conn_context, OP_ID)

    @pytest.mark.asyncio
    async def test_on_connect(self, server, conn_context):
        payload = {}
        await server.on_connect(conn_context, payload)

    @pytest.mark.asyncio
    async def test_on_connection_init(self, server, conn_context):
        exc = Exception("err")
        server.on_connect = AsyncMock()
        server.send_message = AsyncMock(side_effect=exc)
        server.send_error = AsyncMock()

        op_id = 0
        payload = "test message"
        await server.on_connection_init(conn_context, op_id, payload)

        server.on_connect.assert_called_once_with(conn_context, payload)
        server.send_message.assert_called_once_with(
            conn_context, None, GQLMsgType.CONNECTION_ACK, None
        )
        server.send_error.assert_called_once_with(
            conn_context, op_id, exc, GQLMsgType.CONNECTION_ERROR
        )
        conn_context.close_code = WS_INTERNAL_ERROR

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("message", "exc"),
        [
            pytest.param(
                object(),
                TypeError(
                    "the JSON object must be str, bytes or bytearray, "
                    "not object"
                ),
            ),
            pytest.param("[1234]", TypeError("Message must be an object")),
            pytest.param("{}", ValueError("None is not a valid GQLMsgType")),
        ],
    )
    async def test_on_message__bad_message(
        self, server, conn_context, message, exc
    ):
        server.send_error = AsyncMock()
        task = asyncio.ensure_future(server.on_message(conn_context, message))
        await asyncio.sleep(.01)

        assert server.send_error.call_count == 1
        call_args = server.send_error.call_args[0]
        assert call_args[0:2] == (conn_context, None)
        assert isinstance(call_args[2], type(exc))
        assert call_args[2].args[0] == exc.args[0]
        assert task.done() and not task.exception()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("raises"), (True, False))
    async def test_on_message__connection_init(
        self, server, conn_context, raises
    ):
        message = dict(
            type=GQLMsgType.CONNECTION_INIT.value, payload={"test": "data"}
        )
        if raises:
            server.on_connect = Mock(side_effect=Exception("test"))

        await server.on_message(conn_context, json.dumps(message))
        sent = await conn_context.send_queue.get()
        if raises:
            assert json.loads(sent) == {
                "type": GQLMsgType.CONNECTION_ERROR.value,
                "payload": {"message": "test"},
            }
            assert conn_context.close_code == WS_INTERNAL_ERROR
        else:
            assert json.loads(sent) == {"type": GQLMsgType.CONNECTION_ACK.value}

    @pytest.mark.asyncio
    async def test_on_message__connection_terminate(self, server, conn_context):
        message = dict(id=OP_ID, type=GQLMsgType.CONNECTION_TERMINATE.value)
        await server.on_message(conn_context, json.dumps(message))
        assert not conn_context.send_queue.qsize()
        assert conn_context.close_code == WS_INTERNAL_ERROR

    @pytest.mark.asyncio
    async def test_on_message__start_query(self, server, conn_context):
        conn_context.context_value = {"name": "Jack"}
        message = {
            "type": GQLMsgType.START.value,
            "id": OP_ID,
            "payload": {
                "query": (
                    "query getName($title: String!) { name(title: $title) }"
                ),
                "operationName": "getName",
                "variables": {"title": "Mr."},
            },
        }
        await server.on_message(conn_context, json.dumps(message))
        sent = await conn_context.send_queue.get()
        assert json.loads(sent) == {
            "id": OP_ID,
            "type": GQLMsgType.DATA.value,
            "payload": {"data": {"name": "Mr. Jack :: getName"}},
        }

    @pytest.mark.asyncio
    async def test_on_message__start_subscription(self, server, conn_context):
        conn_context.context_value = {"event": asyncio.Event()}

        message = {
            "type": GQLMsgType.START.value,
            "id": OP_ID,
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

        task = asyncio.ensure_future(
            server.on_message(conn_context, json.dumps(message))
        )
        await asyncio.sleep(.01)
        assert OP_ID in conn_context

        for i in range(5):
            conn_context.context_value["event"].set()
            sent = await conn_context.send_queue.get()
            assert json.loads(sent) == {
                "id": OP_ID,
                "type": GQLMsgType.DATA.value,
                "payload": {"data": {"counter": f"{i} :: subscribeCounter"}},
            }

        conn_context.context_value["event"].set()
        sent = await conn_context.send_queue.get()
        assert json.loads(sent) == {
            "id": OP_ID,
            "type": GQLMsgType.COMPLETE.value,
        }

        # operation is closed, now close the connection
        await asyncio.sleep(.01)
        assert OP_ID not in conn_context
        assert task.done() and not task.cancelled() and not task.exception()

    @pytest.mark.asyncio
    async def test_on_message__start_subscription_exists(
        self, server, conn_context, monkeypatch
    ):
        conn_context.context_value = {"event": asyncio.Event()}

        def raising_resolve(root, info, *args, **kwargs):
            # pylint: disable=W0613, unused-argument
            raise Exception("test")

        monkeypatch.setattr(
            schema.get_type("RootSubscriptionType").fields["counter"],
            "resolve",
            raising_resolve,
        )

        start_message = {
            "type": GQLMsgType.START.value,
            "id": OP_ID,
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
        start_task1 = asyncio.ensure_future(
            server.on_message(conn_context, json.dumps(start_message))
        )
        await asyncio.sleep(.01)
        assert OP_ID in conn_context
        start_task1_asyncgen = conn_context[OP_ID]

        start_task2 = asyncio.ensure_future(
            server.on_message(conn_context, json.dumps(start_message))
        )
        await asyncio.sleep(.01)
        assert OP_ID in conn_context
        start_task2_asyncgen = conn_context[OP_ID]

        assert start_task1_asyncgen != start_task2_asyncgen
        await asyncio.sleep(.1)

        assert start_task1.done() and start_task1.cancelled()
        assert not start_task2.done()

        assert not conn_context.send_queue.qsize()
        start_task2.cancel()
        await asyncio.sleep(.01)

        # anext() in graphql_core_next's map_async_iterator hangs until gc.
        gc.collect()

    @pytest.mark.asyncio
    async def test_on_message__stop(self, server, conn_context):
        conn_context.context_value = {"event": asyncio.Event()}

        start_message = {
            "type": GQLMsgType.START.value,
            "id": OP_ID,
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
        start_task = asyncio.ensure_future(
            server.on_message(conn_context, json.dumps(start_message))
        )
        await asyncio.sleep(.01)
        assert OP_ID in conn_context

        stop_message = {"type": GQLMsgType.STOP.value, "id": OP_ID}
        stop_task = asyncio.ensure_future(
            server.on_message(conn_context, json.dumps(stop_message))
        )

        await asyncio.sleep(.01)
        assert stop_task.done() and not stop_task.cancelled()
        assert start_task.done() and start_task.cancelled()
        # anext() in graphql_core_next's map_async_iterator hangs until gc.
        gc.collect()

        sent = await conn_context.send_queue.get()
        assert json.loads(sent) == {
            "type": GQLMsgType.COMPLETE.value,
            "id": OP_ID,
        }
