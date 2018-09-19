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


async def subscribe_counter(
    root: None, info: graphql.GraphQLResolveInfo, ceil: int
):
    # pylint: disable=W0613, unused-argument
    i = 0
    while i < ceil:
        await info.context["event"].wait()
        yield i
        i += 1


def resolve_counter(root: float, info: graphql.GraphQLResolveInfo, ceil: int):
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
    subscription=graphql.GraphQLObjectType(
        name="RootSubscriptionType",
        fields={
            "counter": graphql.GraphQLField(
                graphql.GraphQLString,
                resolve=resolve_counter,
                subscribe=subscribe_counter,
                args={
                    "ceil": graphql.GraphQLArgument(
                        graphql.GraphQLNonNull(graphql.GraphQLInt)
                    )
                },
            )
        },
    ),
)
