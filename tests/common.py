from unittest.mock import Mock

import graphql


class AsyncMock(Mock):
    async def __call__(self, *args, **kwargs):
        # pylint: disable=W0235, useless-super-delegation
        return super().__call__(*args, **kwargs)


def resolve_name(root: None, info: graphql.GraphQLResolveInfo, title: str):
    # pylint: disable=W0613, unused-argument
    name = info.context["name"]
    op_name = info.operation.name.value
    return f"{title} {name} :: {op_name}"


def resolve_set_name(root: None, info: graphql.GraphQLResolveInfo, name: str):
    # pylint: disable=W0613, unused-argument
    info.context["name"] = name
    op_name = info.operation.name.value
    return f"{name} :: {op_name}"


async def subscribe_queue(
    root: None, info: graphql.GraphQLResolveInfo, multiplier: float
):
    # pylint: disable=W0613, unused-argument
    while True:
        number = await info.context["queue"].get()
        if number >= 5:
            break
        yield number * multiplier


def resolve_queue(
    root: float, info: graphql.GraphQLResolveInfo, multiplier: float
):
    # pylint: disable=W0613, unused-argument
    return f"{root} :: {info.operation.name.value}"


schema = graphql.GraphQLSchema(
    query=graphql.GraphQLObjectType(
        name="RootQueryType",
        fields={
            "name": graphql.GraphQLField(
                graphql.GraphQLString,
                resolve=resolve_name,
                args={
                    "title": graphql.GraphQLArgument(
                        graphql.GraphQLNonNull(graphql.GraphQLString)
                    )
                },
            )
        },
    ),
    mutation=graphql.GraphQLObjectType(
        name="RootMutationType",
        fields={
            "setName": graphql.GraphQLField(
                graphql.GraphQLString,
                resolve=resolve_set_name,
                args={
                    "name": graphql.GraphQLArgument(
                        graphql.GraphQLNonNull(graphql.GraphQLString)
                    )
                },
            )
        },
    ),
    subscription=graphql.GraphQLObjectType(
        name="RootSubscriptionType",
        fields={
            "queue": graphql.GraphQLField(
                graphql.GraphQLString,
                resolve=resolve_queue,
                subscribe=subscribe_queue,
                args={
                    "multiplier": graphql.GraphQLArgument(
                        graphql.GraphQLNonNull(graphql.GraphQLFloat)
                    )
                },
            )
        },
    ),
)
