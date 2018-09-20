import asyncio
import json
import typing

import graphql

from graphql_ws.abc import AbstractConnectionContext
from graphql_ws.protocol import (
    WS_INTERNAL_ERROR,
    GQLMsgType,
    OperationMessage,
    OperationMessagePayload,
)


async def close_cancelling(agen):
    while True:
        try:
            task = asyncio.ensure_future(agen.__anext__())
            await task
            yield task.result()
        except (GeneratorExit, StopAsyncIteration):
            await agen.aclose()
            task.cancel()
            break


class ConnectionClosed(Exception):
    pass


class SubscriptionServer:
    schema: graphql.GraphQLSchema
    connection_context_cls: AbstractConnectionContext

    def __init__(self, schema, connection_context_cls):
        self.schema = schema
        self.connection_context_cls = connection_context_cls

    async def handle(self, ws: typing.Any, context_value: typing.Any) -> None:
        connection_context = self.connection_context_cls(ws, context_value)
        await asyncio.shield(self._handle(connection_context))

    async def _handle(self, connection_context: AbstractConnectionContext):
        await self.on_open(connection_context)
        while True:
            try:
                message = await connection_context.receive()
            except ConnectionClosed:
                break
            else:
                connection_context.tasks.add(
                    asyncio.ensure_future(
                        self.on_message(connection_context, message)
                    )
                )
            finally:
                connection_context.tasks = {
                    task for task in connection_context.tasks if not task.done()
                }

        await self.on_close(connection_context)
        for task in connection_context.tasks:
            task.cancel()

    async def send_message(
        self,
        connection_context: AbstractConnectionContext,
        op_id: typing.Optional[str],
        type: GQLMsgType,
        payload: typing.Any,
    ) -> None:
        # pylint: disable=W0622, redefined-builtin
        message = {"type": type.value}
        if op_id is not None:
            message["id"] = op_id
        if payload is not None:
            message["payload"] = payload

        data = json.dumps(message)
        await connection_context.send(data)

    async def send_error(
        self,
        connection_context: AbstractConnectionContext,
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
        await self.send_message(
            connection_context, op_id, error_type, error_payload
        )

    async def send_execution_result(
        self,
        connection_context: AbstractConnectionContext,
        op_id: str,
        execution_result: graphql.ExecutionResult,
    ) -> None:
        result = {}
        if execution_result.data:
            result["data"] = execution_result.data
        if execution_result.errors:
            result["errors"] = [
                graphql.format_error(error) for error in execution_result.errors
            ]
        return await self.send_message(
            connection_context, op_id, GQLMsgType.DATA, result
        )

    async def unsubscribe(
        self, connection_context: AbstractConnectionContext, op_id: str
    ) -> None:
        operation = connection_context.get(op_id)
        if operation:
            await operation.aclose()
        await self.on_operation_complete(connection_context, op_id)

    # ON methods
    async def on_close(
        self, connection_context: AbstractConnectionContext
    ) -> None:
        if not connection_context:
            return
        await asyncio.wait(
            [
                self.unsubscribe(connection_context, op_id)
                for op_id in connection_context
            ]
        )

    async def on_connect(
        self,
        connection_context: AbstractConnectionContext,
        payload: typing.Dict[str, typing.Any],
    ) -> None:
        pass

    async def on_connection_init(
        self,
        connection_context: AbstractConnectionContext,
        op_id: str,
        payload: typing.Dict[str, typing.Any],
    ) -> None:
        try:
            await self.on_connect(connection_context, payload)
            await self.send_message(
                connection_context, None, GQLMsgType.CONNECTION_ACK, None
            )
        except Exception as exc:  # pylint: disable=W0703, broad-except
            await self.send_error(
                connection_context, op_id, exc, GQLMsgType.CONNECTION_ERROR
            )
            await connection_context.close(WS_INTERNAL_ERROR)

    async def on_connection_terminate(
        self, connection_context: AbstractConnectionContext
    ) -> None:
        await connection_context.close(WS_INTERNAL_ERROR)

    async def on_message(
        self, connection_context: AbstractConnectionContext, message: str
    ) -> None:
        try:
            loaded = OperationMessage.loads(message)
        except Exception as e:  # pylint: disable=W0703, broad-except
            await self.send_error(connection_context, None, e)
            return

        if loaded.type is GQLMsgType.CONNECTION_INIT:
            await self.on_connection_init(
                connection_context, loaded.id, loaded.payload
            )

        elif loaded.type is GQLMsgType.CONNECTION_TERMINATE:
            await self.on_connection_terminate(connection_context)

        elif loaded.type is GQLMsgType.START:
            await self.on_start(connection_context, loaded.id, loaded.payload)

        elif loaded.type is GQLMsgType.STOP:
            await self.on_stop(connection_context, loaded.id)

    async def on_open(
        self, connection_context: AbstractConnectionContext
    ) -> None:
        pass

    async def on_operation_complete(
        self, connection_context: AbstractConnectionContext, op_id: str
    ) -> None:
        pass

    async def on_start(
        self,
        connection_context: AbstractConnectionContext,
        op_id: str,
        payload: OperationMessagePayload,
    ) -> None:
        """
        We shield the graphql executions as cancelling semi-complete executions
        can lead to inconsistent behavior (for example partial transactions)
        """
        # If we already have a sub with this id, unsubscribe from it first
        if op_id in connection_context:
            await self.unsubscribe(connection_context, op_id)

        if payload.has_subscription_operation:
            result = await graphql.subscribe(
                self.schema,
                document=payload.document,
                context_value=connection_context.context_value,
                variable_values=payload.variable_values,
                operation_name=payload.operation_name,
            )
        else:
            result = await graphql.graphql(
                self.schema,
                source=payload.source,
                context_value=connection_context.context_value,
                variable_values=payload.variable_values,
                operation_name=payload.operation_name,
            )

        if not isinstance(result, typing.AsyncIterator):
            await self.send_execution_result(connection_context, op_id, result)
            return

        # agen = connection_context[op_id] = close_cancelling(result)
        connection_context[op_id] = result
        try:
            async for val in result:  # pylint: disable=E1133, not-an-iterable
                await self.send_execution_result(connection_context, op_id, val)
        finally:
            if connection_context.get(op_id) == result:
                del connection_context[op_id]
                await self.send_message(
                    connection_context, op_id, GQLMsgType.COMPLETE, None
                )

    async def on_stop(
        self, connection_context: AbstractConnectionContext, op_id: str
    ) -> None:
        await self.unsubscribe(connection_context, op_id)
