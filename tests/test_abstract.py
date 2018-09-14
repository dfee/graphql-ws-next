import asyncio

import graphql
import pytest

from graphql_ws.protocol import WS_INTERNAL_ERROR, GQLMsgType
from graphql_ws.abstract import AbstractSubscriptionServer

from .common import AsyncMock

# pylint: disable=C0103, invalid-name
# pylint: disable=R0201, no-self-use
# pylint: disable=W0621, redefined-outer-name


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


@pytest.fixture
def server(schema, context_value):
    class ConcreteSubscriptionServer(AbstractSubscriptionServer):
        closed = False
        __call__ = AsyncMock()
        close = AsyncMock()
        send = AsyncMock()

    return ConcreteSubscriptionServer(schema, context_value)


class TestAbstractSubscriptionServer:
    @pytest.mark.asyncio
    async def test_send_execution_result(self, server):
        server.send_message = AsyncMock()
        op_id = 0

        data = "test data"
        exec_result = graphql.ExecutionResult(data=data, errors=[ERROR])
        await server.send_execution_result(op_id, exec_result)
        expected = dict(data=data, errors=[FORMATTED_ERROR])
        server.send_message.assert_called_once_with(
            op_id, GQLMsgType.DATA, expected
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op_id", "op_type", "payload", "expected"),
        [
            pytest.param(
                0,
                GQLMsgType.DATA,
                "test data",
                {"id": 0, "type": "data", "payload": "test data"},
                id="complete",
            ),
            pytest.param(
                None, None, "test data", {"payload": "test data"}, id="minimal"
            ),
            pytest.param(
                None,
                None,
                None,
                AssertionError("You need to send at least one thing"),
                id="empty",
            ),
        ],
    )
    async def test_send_message(
        self, server, op_id, op_type, payload, expected
    ):
        if isinstance(expected, Exception):
            with pytest.raises(type(expected)) as exctype:
                await server.send_message(op_id, op_type, payload)
            assert exctype.value.args[0] == expected.args[0]
            server.send.assert_not_called()
        else:
            await server.send_message(op_id, op_type, payload)
            server.send.assert_called_once_with(expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op_id", "error", "error_type", "expected"),
        [
            pytest.param(
                0,
                Exception("test exc"),
                GQLMsgType.ERROR,
                (0, GQLMsgType.ERROR, {"message": "test exc"}),
                id="complete",
            ),
            pytest.param(
                None,
                Exception("test exc"),
                None,
                (None, GQLMsgType.ERROR, {"message": "test exc"}),
                id="minimal",
            ),
            pytest.param(
                None,
                Exception(),
                GQLMsgType.DATA,
                AssertionError(
                    "error_type should be one of the allowed error messages "
                    "GQLMessageType.CONNECTION_ERROR or GQLMsgType.ERROR"
                ),
                id="bad_error_type",
            ),
        ],
    )
    async def test_send_error(self, server, op_id, error, error_type, expected):
        server.send_message = AsyncMock()
        if isinstance(expected, Exception):
            with pytest.raises(type(expected)) as exctype:
                await server.send_error(op_id, error, error_type)
            assert exctype.value.args[0] == expected.args[0]
            server.send_message.assert_not_called()
        else:
            await server.send_error(op_id, error, error_type)
            server.send_message.assert_called_once_with(*expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("registered", (False, True))
    async def test_unsubscribe(self, server, registered):
        server.on_operation_complete = AsyncMock()
        op_id = 0
        task = None
        if registered:
            event = asyncio.Event()
            task = server.operations[op_id] = asyncio.ensure_future(
                event.wait()
            )

        await server.unsubscribe(op_id)

        if registered:
            await asyncio.sleep(.01)
            assert task.cancelled()

        server.on_operation_complete.assert_called_once_with(op_id)

    @pytest.mark.asyncio
    async def test_on_close(self, server):
        event = asyncio.Event()
        task = asyncio.ensure_future(event.wait())
        server.operations[0] = task

        await server.on_close()

        assert not server.operations
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_on_connect(self):
        pass

    @pytest.mark.asyncio
    async def test_on_connection_init(self, server):
        exc = Exception("err")
        server.on_connect = AsyncMock()
        server.send_message = AsyncMock(side_effect=exc)
        server.send_error = AsyncMock()

        op_id = 0
        payload = "test message"
        await server.on_connection_init(op_id, payload)

        server.on_connect.assert_called_once_with(payload)
        server.send_message.assert_called_once_with(
            op_type=GQLMsgType.CONNECTION_ACK
        )
        server.send_error.assert_called_once_with(
            op_id, exc, GQLMsgType.CONNECTION_ERROR
        )
        server.close.assert_called_once_with(WS_INTERNAL_ERROR)

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
            pytest.param(
                "[1234]", AssertionError("The message must be a dict")
            ),
        ],
    )
    async def test_on_message__bad_message(self, server, message, exc):
        server.send_error = AsyncMock()
        await server.on_message(message)
        assert server.send_error.call_count == 1
        call_args = server.send_error.call_args[0]
        assert call_args[0] is None
        assert type(call_args[1]) == type(exc)
        assert isinstance(exc, type(call_args[1]))
        assert exc.args[0] == call_args[1].args[0]

    @pytest.mark.asyncio
    async def test_on_message__bad_op_type(self, server):
        server.send_error = AsyncMock()
        message = dict(id=0, type="unknown", payload=object())
        await server.on_message(message)
        call_args = server.send_error.call_args[0]
        assert call_args[0] is 0
        exc = Exception("Invalid message type: 'unknown'")
        assert type(call_args[1]) == type(exc)
        assert isinstance(exc, type(call_args[1]))

    @pytest.mark.asyncio
    async def test_on_message__connection_init(self, server):
        message = dict(id=0, type=GQLMsgType.CONNECTION_INIT, payload=object())
        server.on_connection_init = AsyncMock()
        await server.on_message(message)
        server.on_connection_init.assert_called_once_with(
            message["id"], message["payload"]
        )

    @pytest.mark.asyncio
    async def test_on_message__connection_terminate(self, server):
        message = dict(
            id=0, type=GQLMsgType.CONNECTION_TERMINATE, payload=object()
        )
        server.on_connection_terminate = AsyncMock()
        await server.on_message(message)
        server.on_connection_terminate.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_on_message__start__invalid(self, server):
        message = dict(id=0, type=GQLMsgType.START, payload=object())
        with pytest.raises(AssertionError) as exctype:
            await server.on_message(message)
        assert exctype.value.args[0] == "The payload must be a dict"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exists", (False, True))
    async def test_on_message__start(self, server, exists):
        server.on_start = AsyncMock()
        server.unsubscribe = AsyncMock()

        message = dict(id=0, type=GQLMsgType.START, payload={"test": "data"})
        task = None
        if exists:
            event = asyncio.Event()
            task = asyncio.ensure_future(event.wait())
            server.operations[message["id"]] = task

        await server.on_message(message)
        await asyncio.sleep(.01)
        server.on_start.assert_called_once_with(
            message["id"], message["payload"]
        )

        if exists:
            server.unsubscribe.assert_called_once_with(message["id"])
            task.cancel()
            await asyncio.sleep(.01)

    @pytest.mark.asyncio
    async def test_on_message__stop(self, server):
        server.on_stop = AsyncMock()
        message = dict(id=0, type=GQLMsgType.STOP, payload=object())
        await server.on_message(message)
        server.on_stop.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_on_open(self):
        pass

    @pytest.mark.asyncio
    async def test_on_operation_complete(self):
        pass

    @pytest.mark.asyncio
    async def test_on_start__missing(self, context_value, server, monkeypatch):
        mock_graphql = AsyncMock()
        monkeypatch.setattr(graphql, "graphql", mock_graphql)
        server.send_execution_result = AsyncMock()

        await server.on_start(0, {})

        # pylint: disable=E1101, no-member
        assert graphql.graphql.call_count == 1
        call_kwargs = graphql.graphql.call_args[1]
        assert call_kwargs["context_value"] == context_value
        assert call_kwargs["variable_values"] is None
        assert call_kwargs["operation_name"] is None

        blank = graphql.Source(None)
        assert call_kwargs["source"].body == blank.body
        assert call_kwargs["source"].location_offset == blank.location_offset
        assert call_kwargs["source"].name == blank.name

        server.send_execution_result.assert_called_once_with(
            0, mock_graphql.return_value
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancelled", (False, True))
    @pytest.mark.parametrize("has_operation", (False, True))
    async def test_on_start__subscription(
        self,
        schema,
        context_value,
        server,
        monkeypatch,
        cancelled,
        has_operation,
    ):
        # pylint: disable=R0914, too-many-locals
        closed = False
        was_cancelled = False
        done = False
        event = asyncio.Event()

        async def agenerator():
            try:
                while True:
                    await event.wait()
                    event.clear()
                    if done:
                        return
                    yield 1
            except asyncio.CancelledError:
                nonlocal was_cancelled
                was_cancelled = True
            finally:
                nonlocal closed
                closed = True

        mock_subscribe = AsyncMock(return_value=agenerator())
        monkeypatch.setattr(graphql, "subscribe", mock_subscribe)
        server.send_execution_result = AsyncMock()
        server.send_message = AsyncMock()

        payload = {
            "query": "subscription { time }",
            "variableValues": {"test": 1},
            "operationName": "testSubscription",
        }

        fut = asyncio.ensure_future(server.on_start(0, payload))

        if has_operation:
            server.operations[0] = object()

        await asyncio.sleep(.01)
        event.set()
        await asyncio.sleep(.01)

        mock_subscribe.assert_called_once_with(
            schema,
            document=graphql.parse(payload["query"]),
            context_value=context_value,
            variable_values=payload["variableValues"],
            operation_name=payload["operationName"],
        )

        if has_operation:
            server.send_execution_result.assert_called_once_with(0, 1)

            if cancelled:
                fut.cancel()
                await asyncio.sleep(.01)
            else:
                event.set()
                done = True
                await asyncio.sleep(.01)

            assert was_cancelled == cancelled
            assert closed

        else:
            server.send_execution_result.assert_not_called()

        server.send_message.assert_called_once_with(0, GQLMsgType.COMPLETE)
        assert not server.operations
        assert fut.done()
        assert not fut.exception()

    @pytest.mark.asyncio
    async def test_on_start__non_subscription(
        self, schema, context_value, server, monkeypatch
    ):
        mock_graphql = AsyncMock(return_value=object())
        server.send_execution_result = AsyncMock()

        monkeypatch.setattr(graphql, "graphql", mock_graphql)
        server.send_execution_result = AsyncMock()
        server.send_message = AsyncMock()

        payload = {
            "query": "query { time }",
            "variableValues": {"test": 1},
            "operationName": "testQuery",
        }
        payload_source = graphql.Source(payload["query"])

        await server.on_start(0, payload)

        call_args = {
            **{"schema": mock_graphql.call_args[0][0]},
            **mock_graphql.call_args[1],
        }
        assert call_args["schema"] == schema
        assert call_args["context_value"] == context_value
        assert call_args["variable_values"] == payload["variableValues"]
        assert call_args["operation_name"] == payload["operationName"]

        for name in call_args["source"].__slots__:
            assert getattr(call_args["source"], name) == getattr(
                payload_source, name
            )

    @pytest.mark.asyncio
    async def test_on_stop(self, server):
        server.unsubscribe = AsyncMock()
        await server.on_stop(0)
        server.unsubscribe.assert_called_once_with(0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "query"),
        [
            pytest.param("graphql", "{ test }", id="query"),
            pytest.param(
                "subscribe", "subscription{ test }", id="subscription"
            ),
        ],
    )
    async def test_on_start__shielded(self, server, monkeypatch, query, method):
        event = asyncio.Event()
        completed = False

        async def shielded(*args, **kwargs):
            # pylint: disable=W0613, unused-argument
            await event.wait()
            nonlocal completed
            completed = True

        payload = {"query": query}
        monkeypatch.setattr(graphql, method, shielded)
        fut = asyncio.ensure_future(server.on_start(0, payload))
        await asyncio.sleep(.01)

        fut.cancel()
        await asyncio.sleep(.01)
        assert fut.done() and fut.cancelled()

        event.set()
        assert not completed
        await asyncio.sleep(.01)
        assert completed
