import collections.abc
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


class OperationMessagePayload(collections.abc.Mapping):
    __slots__ = ("_payload",)

    def __init__(self, payload: typing.Dict[str, typing.Any]):
        if payload is not None and not isinstance(payload, dict):
            raise TypeError("Payload must be an object")
        self._payload = payload or {}

    def __getitem__(self, key: str) -> typing.Any:
        return self._payload[key]

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def query(self):
        return self.get("query")

    @property
    def variable_values(self):
        return self.get("variableValues")

    @property
    def operation_name(self):
        return self.get("operationName")

    @property
    def document(self) -> typing.Optional[graphql.DocumentNode]:
        try:
            return graphql.parse(self.query)
        except Exception:  # pylint: disable=W0703, broad-except
            return None

    @property
    def source(self) -> graphql.Source:
        return graphql.Source(self.query)

    @property
    def has_subscription_operation(self) -> bool:
        document = self.document
        if not document:
            return False
        return any(
            [
                definition.operation is graphql.OperationType.SUBSCRIPTION
                for definition in self.document.definitions
            ]
        )


class OperationMessage:
    __slots__ = ("_type", "_id", "_payload")

    _type: GQLMsgType
    _id: typing.Optional[str]
    _payload: typing.Optional[OperationMessagePayload]

    def __init__(self, type, id=None, payload=None):
        # pylint: disable=W0622, redefined-builtin
        self._type = GQLMsgType(type)
        self._id = id
        self._payload = OperationMessagePayload(payload)

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, type(self)):
            return False
        return all(
            [
                self.type == other.type,
                self.id == other.id,
                self.payload == other.payload,
            ]
        )

    @property
    def id(self) -> str:
        return self._id

    @property
    def type(self) -> GQLMsgType:
        return self._type

    @property
    def payload(self) -> OperationMessagePayload:
        return self._payload

    @classmethod
    def load(cls, data: typing.Dict[str, typing.Any]) -> "OperationMessage":
        if not isinstance(data, dict):
            raise TypeError("Message must be an object")
        return cls(
            type=data.get("type"),
            id=data.get("id"),
            payload=data.get("payload"),
        )

    @classmethod
    def loads(cls, data: str) -> "OperationMessage":
        return cls.load(json.loads(data))
