import graphql
import pytest

from graphql_ws.protocol import OperationMessage, StartPayload, GQLMsgType

# pylint: disable=R0201, no-self-use


class TestOperationMessage:
    @pytest.mark.parametrize(
        ("id", "type", "payload", "expected"),
        [
            pytest.param(
                "0",
                GQLMsgType.DATA,
                {"query": "{ test }"},
                '{"id": "0", "type": "data", "payload": {"query": "{ test }"}}',
                id="complete",
            ),
            pytest.param(None, None, None, "{}", id="minimal"),
        ],
    )
    def test_serialize(self, id, type, payload, expected):
        # pylint: disable=W0622, redefined-builtin
        op_msg = OperationMessage(id=id, type=type, payload=payload).serialize()
        assert op_msg == expected

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            pytest.param(
                '{"id": "0", "type": "data", "payload": {"query": "{ test }"}}',
                OperationMessage("0", GQLMsgType.DATA, {"query": "{ test }"}),
                id="complete",
            )
        ],
    )
    def test_deserialize(self, data, expected):
        # pylint: disable=W0622, redefined-builtin
        op_msg = OperationMessage.deserialize(data)
        assert op_msg == expected


class TestStartPayload:
    @pytest.mark.parametrize(
        ("query", "is_subscription"),
        [
            pytest.param(None, False, id="missing"),
            pytest.param("{ test }", False, id="query"),
            pytest.param("subscription { test }", True, id="subscription"),
        ],
    )
    def test_load_query(self, query, is_subscription):
        provided = {"variableValues": {"a": 1}, "operationName": "test"}
        if query:
            provided["query"] = query

        returned = StartPayload.load(provided)
        expected = StartPayload(
            query=provided.get("query"),
            source=graphql.Source(provided.get("query")),
            document=graphql.parse(graphql.Source(provided["query"]))
            if query
            else None,
            variable_values=provided["variableValues"],
            operation_name=provided["operationName"],
        )
        assert returned == expected
        assert returned.has_subscription_operation == is_subscription

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            pytest.param(None, None, id="no_override"),
            pytest.param("query", "{ test2 }", id="query"),
            pytest.param("source", graphql.Source("{ test2 }"), id="source"),
            pytest.param("document", graphql.parse("{ test2 }"), id="document"),
            pytest.param("variable_values", {"b": 2}, id="variable_values"),
            pytest.param("operation_name", "test2", id="operation_name"),
        ],
    )
    def test__eq__(self, key, value):
        kwargs = {
            "query": "{ test }",
            "source": graphql.Source("{ test }"),
            "document": graphql.parse("{ test }"),
            "variable_values": {"a": 1},
            "operation_name": "test",
        }
        payload1 = StartPayload(**kwargs)
        payload2 = (
            StartPayload(**{**kwargs, **{key: value}})
            if key
            else StartPayload(**kwargs)
        )
        assert (payload1 == payload2) is not bool(key)
