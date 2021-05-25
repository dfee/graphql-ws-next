import asyncio
from datetime import datetime
import json

from aiohttp import web
from graphql import (
    GraphQLField,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    graphql,
)

import graphql_ws
from graphql_ws.aiohttp import AiohttpConnectionContext

from demo.graphiql_template import make_template


def resolve_time(root, info):
    return datetime.now().isoformat()


async def subscribe_time(root, info):
    while True:
        yield {"time": datetime.now().isoformat()}
        await asyncio.sleep(1)


schema = GraphQLSchema(
    query=GraphQLObjectType(
        "RootQueryType",
        {
            "time": GraphQLField(
                GraphQLNonNull(GraphQLString), resolve=resolve_time
            )
        },
    ),
    subscription=GraphQLObjectType(
        "RootSubscriptionType",
        {
            "time": GraphQLField(
                GraphQLNonNull(GraphQLString), subscribe=subscribe_time
            )
        },
    ),
)


async def handle_root(request):
    return web.Response(text=make_template(), content_type="text/html")


async def handle_subscriptions(request):
    wsr = web.WebSocketResponse(protocols=(graphql_ws.WS_PROTOCOL,))
    request.app["websockets"].add(wsr)
    await wsr.prepare(request)
    await request.app["subscription_server"].handle(wsr, None)
    request.app["websockets"].remove(wsr)
    return wsr


async def handle_graphql(request: web.Request) -> web.Response:
    response = web.Response(content_type="application/json")

    content_type = request.content_type
    req_payload = dict()
    if content_type == "application/graphql":
        req_payload.update(query=await request.text())
    elif content_type == "application/json":
        req_payload.update(**json.loads(await request.text()))
    elif request.content_type in (
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ):
        req_payload.update(await request.post())

    result = await graphql(
        schema,
        source=req_payload.get("query"),
        variable_values=req_payload.get("variables"),
        operation_name=req_payload.get("operationName"),
    )

    res_payload = dict(data=result.data)
    if result.errors:
        res_payload["errors"] = [error.formatted for error in result.errors]

    response.text = json.dumps(res_payload)
    return response


def make_app():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/graphql", handle_graphql)
    app.router.add_post("/graphql", handle_graphql)
    app.router.add_get("/subscriptions", handle_subscriptions)

    app["subscription_server"] = graphql_ws.SubscriptionServer(
        schema, AiohttpConnectionContext
    )
    app["websockets"] = set()

    async def on_shutdown(app):
        await asyncio.wait([wsr.close() for wsr in app["websockets"]])

    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    app = make_app()
    web.run_app(app)
