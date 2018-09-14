import asyncio

import pytest

from graphql_ws.protocol import GQLMsgType
from graphql_ws.testing import (
    TestSubscriptionServer as _TestSubscriptionServer,
    TestWebsocketTransport as _TestWebsocketTransport,
)

from .common import schema

# pylint: disable=W0621, redefined-outer-name


@pytest.fixture
def context_value():
    return {"name": "Jack", "queue": asyncio.Queue()}


@pytest.fixture
async def transport():
    return _TestWebsocketTransport()


@pytest.fixture
async def client(transport):
    return transport.client


@pytest.fixture
async def server(transport, context_value):
    test_server = _TestSubscriptionServer(
        transport.server, schema, context_value
    )
    fut = asyncio.ensure_future(test_server())
    yield test_server
    fut.cancel()


@pytest.mark.asyncio
async def test_query_operation(server, client):
    message = dict(
        id=0,
        type=GQLMsgType.START.value,
        payload=dict(
            query="""
            query nameQuery ($title: String!) {
                name(title: $title)
            }
            """,
            operationName="nameQuery",
            variableValues={"title": "Mr."},
        ),
    )

    await client.send_json(message)
    await asyncio.sleep(.01)
    resp = await client.receive_json()
    await asyncio.sleep(.01)

    assert resp == dict(
        id=0,
        type=GQLMsgType.DATA.value,
        payload={"data": {"name": "Mr. Jack :: nameQuery"}},
    )
    assert not server.operations


@pytest.mark.asyncio
async def test_mutation_operation(server, client, context_value):
    message = dict(
        id=0,
        type=GQLMsgType.START.value,
        payload=dict(
            query="""
            mutation setNameMutation ($name: String!) {
                setName(name: $name)
            }
            """,
            operationName="setNameMutation",
            variableValues={"name": "John"},
        ),
    )

    await client.send_json(message)
    resp = await client.receive_json()

    assert resp == dict(
        id=0,
        type=GQLMsgType.DATA.value,
        payload={"data": {"setName": "John :: setNameMutation"}},
    )
    assert context_value["name"] == "John"
    assert not server.operations


@pytest.mark.asyncio
async def test_subscription_operation(client, server, context_value):
    message = dict(
        id=0,
        type=GQLMsgType.START.value,
        payload=dict(
            query="""
            subscription queueSubscription ($multiplier: Float!) {
                queue(multiplier: $multiplier)
            }
            """,
            operationName="queueSubscription",
            variableValues={"multiplier": 0.5},
        ),
    )
    await client.send_json(message)

    for i in range(0, 5):
        await context_value["queue"].put(i)
        await asyncio.sleep(0.01)
        msg = await client.receive_json()
        assert msg == {
            "id": 0,
            "type": "data",
            "payload": {"data": {"queue": f"{0.5 * i} :: queueSubscription"}},
        }

    await context_value["queue"].put(6)
    msg = await client.receive_json()
    assert msg == {"id": 0, "type": "complete"}

    assert not server.operations
