import enum
import json
import typing

import graphql

WS_INTERNAL_ERROR = 1011
WS_PROTOCOL = "graphql-ws"


class GQLMsgType(enum.Enum):
    CONNECTION_INIT = "connection_init"  # Client -> Server
    CONNECTION_ACK = "connection_ack"  # Server -> Client
    CONNECTION_ERROR = "connection_error"  # Server -> Client

    # NOTE: The keep alive message type does not follow the standard due to
    # connection optimizations
    CONNECTION_KEEP_ALIVE = "ka"  # Server -> Client

    CONNECTION_TERMINATE = "connection_terminate"  # Client -> Server
    START = "start"  # Client -> Server
    DATA = "data"  # Server -> Client
    ERROR = "error"  # Server -> Client
    COMPLETE = "complete"  # Server -> Client
    STOP = "stop"  # Client -> Server


class OperationMessage(typing.NamedTuple):
    id: typing.Union[str, None] = None
    type: typing.Union[GQLMsgType, None] = None
    payload: typing.Any = None

    def serialize(self) -> str:
        data = {}
        if self.id:
            data["id"] = self.id
        if self.type:
            data["type"] = self.type.value
        if self.type:
            data["payload"] = self.payload
        return json.dumps(data)

    @classmethod
    def deserialize(cls, data: str) -> "OperationMessage":
        loaded = json.loads(data)
        return OperationMessage(
            id=loaded.get("id"),
            type=GQLMsgType(loaded.get("type")),
            payload=loaded.get("payload"),
        )


class StartPayload(typing.NamedTuple):
    query: typing.Optional[str]
    source: graphql.Source
    document: typing.Optional[graphql.DocumentNode]
    variable_values: typing.Optional[typing.Dict[str, typing.Any]]
    operation_name: typing.Optional[str]

    @classmethod
    def load(cls, payload: typing.Dict[str, typing.Any]) -> "StartPayload":
        query = payload.get("query")
        source = graphql.Source(query)
        try:
            document = graphql.parse(source)
        except Exception:  # pylint: disable=W0703, broad-except
            document = None

        return cls(
            query=query,
            source=source,
            document=document,
            variable_values=payload.get("variableValues"),
            operation_name=payload.get("operationName"),
        )

    @property
    def has_subscription_operation(self) -> bool:
        if not self.document:
            return False
        return any(
            [
                definition.operation is graphql.OperationType.SUBSCRIPTION
                for definition in self.document.definitions
            ]
        )

    def __eq__(self, other: typing.Any):
        if not isinstance(other, type(self)):
            return False
        return all(
            [
                self.query == other.query,
                self.source.body == other.source.body,
                self.source.location_offset == other.source.location_offset,
                self.source.name == other.source.name,
                self.document == other.document,
                self.variable_values == other.variable_values,
                self.operation_name == other.operation_name,
            ]
        )
