import graphql
import pytest

from graphql_ws.protocol import (
    OperationMessage,
    OperationMessagePayload,
    GQLMsgType,
)

# pylint: disable=R0201, no-self-use


class TestOperationMessagePayload:
    def test_attrs(self):
        params = {
            "query": "query myTest { test }",
            "variableValues": {"test": 1},
            "operationName": "myTest",
        }
        payload = OperationMessagePayload(params)
        assert payload.query == params["query"]
        assert payload.variable_values == params["variableValues"]
        assert payload.operation_name == params["operationName"]

    @pytest.mark.parametrize(
        ("raw", "source", "document", "has_subscription_operation"),
        [
            pytest.param(
                "{ test }",
                graphql.Source("{ test }"),
                graphql.parse("{ test }"),
                False,
                id="query",
            ),
            pytest.param(
                "subscription { test }",
                graphql.Source("subscription { test }"),
                graphql.parse("subscription { test }"),
                True,
                id="subscription",
            ),
            pytest.param(None, graphql.Source(None), None, False, id="missing"),
        ],
    )
    def test_parsing(self, raw, source, document, has_subscription_operation):
        payload = OperationMessagePayload({"query": raw})

        assert payload.source.body == source.body
        assert payload.source.location_offset == source.location_offset
        assert payload.source.name == source.name

        assert payload.document == document

        assert payload.has_subscription_operation == has_subscription_operation

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            pytest.param(None, None, id="no_override"),
            pytest.param("query", "{ test2 }", id="query"),
            pytest.param("variableValues", {"b": 2}, id="variable_values"),
            pytest.param("operationName", "test2", id="operation_name"),
        ],
    )
    def test__eq__(self, key, value):
        kwargs = {
            "query": "{ test }",
            "variableValues": {"a": 1},
            "operationName": "test",
        }
        payload1 = OperationMessagePayload(kwargs)
        payload2 = (
            OperationMessagePayload({**kwargs, **{key: value}})
            if key
            else OperationMessagePayload(kwargs)
        )
        assert (payload1 == payload2) is not bool(key)


class TestOperationMessage:
    @pytest.mark.parametrize(
        ("type", "id", "payload"),
        [
            pytest.param("start", "0", {"query": "{ _ }"}, id="conversion"),
            pytest.param("connection_init", None, None, id="partial"),
        ],
    )
    def test_structure(self, type, id, payload):
        # pylint: disable=W0622, redefined-builtin
        om = OperationMessage(type=type, id=id, payload=payload)
        assert isinstance(om.type, GQLMsgType)
        assert om.type.value == type
        assert om.id == id

        assert isinstance(om.payload, OperationMessagePayload)
        assert dict(om.payload) == (payload or {})

    def test_load(self):
        data = {"type": "start", "id": "0", "payload": {"query": "{ _ }"}}
        loaded = OperationMessage.load(data)
        assert loaded.type == GQLMsgType.START
        assert loaded.id == "0"
        assert loaded.payload == OperationMessagePayload({"query": "{ _ }"})

    @pytest.mark.parametrize(
        ("data", "exc"),
        [
            pytest.param(
                1234,
                TypeError(
                    "the JSON object must be str, bytes or bytearray, not int"
                ),
                id="invalid_json",
            ),
            pytest.param(
                "[]",
                TypeError("Message must be an object"),
                id="non_dict_message",
            ),
            pytest.param(
                "{}",
                ValueError("None is not a valid GQLMsgType"),
                id="missing_type",
            ),
            pytest.param(
                '{"type": "start", "payload": []}',
                TypeError("Payload must be an object"),
                id="invalid_payload",
            ),
            pytest.param(
                '{ "asdf": ',
                ValueError("Expecting value: line 1 column 11 (char 10)"),
                id="malformed_json",
            ),
        ],
    )
    def test_load_raises(self, data, exc):
        with pytest.raises(type(exc)) as exctype:
            OperationMessage.loads(data)
        assert exctype.value.args[0] == exc.args[0]

    def test_loads(self):
        data = '{"type": "start", "id": "0", "payload": {"query": "{ _ }"}}'
        loaded = OperationMessage.loads(data)
        assert loaded.type == GQLMsgType.START
        assert loaded.id == "0"
        assert loaded.payload == OperationMessagePayload({"query": "{ _ }"})
