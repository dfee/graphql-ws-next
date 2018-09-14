from abc import ABC, abstractmethod
import asyncio
import json
import typing

import graphql

from graphql_ws.protocol import WS_INTERNAL_ERROR, GQLMsgType, StartPayload


class AbstractSubscriptionServer(ABC):
    schema = graphql.GraphQLSchema
    context_value = typing.Any
    operations: typing.Dict[typing.Union[int, str], asyncio.Future]

    def __init__(self, schema, context_value):
        self.schema = schema
        self.context_value = context_value
        self.operations = {}

    @abstractmethod
    async def __call__(self) -> None:
        pass

    @property
    @abstractmethod
    def closed(self) -> bool:
        pass

    @abstractmethod
    async def close(self, code: int) -> None:
        pass

    @abstractmethod
    async def send(self, data: typing.Dict[str, typing.Any]) -> None:
        pass

    async def send_execution_result(
        self, op_id: str, execution_result: graphql.ExecutionResult
    ) -> None:
        result = {}
        if execution_result.data:
            result["data"] = execution_result.data
        if execution_result.errors:
            result["errors"] = [
                graphql.format_error(error) for error in execution_result.errors
            ]
        return await self.send_message(op_id, GQLMsgType.DATA, result)

    async def send_message(
        self,
        op_id: typing.Optional[typing.Union[int, str]] = None,
        op_type: typing.Optional[GQLMsgType] = None,
        payload: typing.Any = None,
    ) -> None:
        message = {}
        if op_id is not None:
            message["id"] = op_id
        if op_type is not None:
            message["type"] = op_type.value
        if payload is not None:
            message["payload"] = payload

        assert message, "You need to send at least one thing"
        await self.send(message)

    async def send_error(
        self,
        op_id: typing.Optional[str],
        error: Exception,
        error_type: typing.Optional[GQLMsgType] = None,
    ) -> None:
        if error_type is None:
            error_type = GQLMsgType.ERROR

        assert error_type in [GQLMsgType.CONNECTION_ERROR, GQLMsgType.ERROR], (
            "error_type should be one of the allowed error messages "
            "GQLMessageType.CONNECTION_ERROR or GQLMsgType.ERROR"
        )

        error_payload = {"message": str(error)}
        await self.send_message(op_id, error_type, error_payload)

    async def unsubscribe(self, op_id: typing.Union[int, str]) -> None:
        operation = self.operations.pop(op_id, None)
        if operation:
            operation.cancel()
        await self.on_operation_complete(op_id)

    # ON methods
    async def on_close(self) -> None:
        await asyncio.gather(
            *[self.unsubscribe(op_id) for op_id in self.operations]
        )

    async def on_connect(self, payload: typing.Dict[str, typing.Any]) -> None:
        pass

    async def on_connection_init(
        self, op_id: str, payload: typing.Dict[str, typing.Any]
    ) -> None:
        try:
            await self.on_connect(payload)
            await self.send_message(op_type=GQLMsgType.CONNECTION_ACK)
        except Exception as exc:  # pylint: disable=W0703, broad-except
            await self.send_error(op_id, exc, GQLMsgType.CONNECTION_ERROR)
            await self.close(WS_INTERNAL_ERROR)

    async def on_connection_terminate(self) -> None:
        await self.close(WS_INTERNAL_ERROR)

    async def on_message(
        self, message: typing.Union[typing.Dict[str, typing.Any], str]
    ) -> None:
        try:
            if not isinstance(message, dict):
                parsed_message = json.loads(message)
                assert isinstance(
                    parsed_message, dict
                ), "The message must be a dict"
            else:
                parsed_message = message
        except Exception as exc:  # pylint: disable=W0703, broad-except
            return await self.send_error(None, exc)

        op_id = parsed_message.get("id")
        op_type = parsed_message.get("type")
        try:
            op_type = GQLMsgType(op_type)
        except ValueError:
            await self.send_error(
                op_id, Exception(f"Invalid message type: '{op_type}'")
            )
        else:
            payload = parsed_message.get("payload")

            if op_type is GQLMsgType.CONNECTION_INIT:
                await self.on_connection_init(op_id, payload)

            elif op_type is GQLMsgType.CONNECTION_TERMINATE:
                await self.on_connection_terminate()

            elif op_type is GQLMsgType.START:
                assert isinstance(payload, dict), "The payload must be a dict"

                # If we already have a subscription with this id, unsubscribe
                # from it first
                if op_id in self.operations:
                    await self.unsubscribe(op_id)

                self.operations[op_id] = asyncio.ensure_future(
                    self.on_start(op_id, payload)
                )

            elif op_type is GQLMsgType.STOP:
                await self.on_stop(op_id)

    async def on_open(self) -> None:
        pass

    async def on_operation_complete(self, op_id: str) -> None:
        pass

    async def on_start(
        self,
        op_id: typing.Union[int, str],
        payload: typing.Dict[str, typing.Any],
    ) -> None:
        """
        We shield the graphql executions as cancelling semi-complete executions
        can lead to inconsistent behavior (for example partial transactions)
        """
        payload_ = StartPayload.load(payload)

        if payload_.has_subscription_operation:
            result = await asyncio.shield(
                graphql.subscribe(
                    self.schema,
                    document=payload_.document,
                    context_value=self.context_value,
                    variable_values=payload_.variable_values,
                    operation_name=payload_.operation_name,
                )
            )
        else:
            result = await asyncio.shield(
                graphql.graphql(
                    self.schema,
                    source=payload_.source,
                    context_value=self.context_value,
                    variable_values=payload_.variable_values,
                    operation_name=payload_.operation_name,
                )
            )

        if not isinstance(result, typing.AsyncIterator):
            await self.send_execution_result(op_id, result)
            self.operations.pop(op_id, None)
            return

        try:
            async for val in result:
                if op_id not in self.operations:
                    break
                await self.send_execution_result(op_id, val)
        except asyncio.CancelledError:
            if hasattr(result, "aclose"):
                await result.aclose()
        finally:
            await self.send_message(op_id, GQLMsgType.COMPLETE)
            self.operations.pop(op_id, None)

    async def on_stop(self, op_id: str) -> None:
        await self.unsubscribe(op_id)
