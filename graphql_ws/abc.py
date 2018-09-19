import asyncio
import abc
import collections.abc
import typing


class AbstractConnectionContext(collections.abc.MutableMapping):
    ws: typing.Any
    context_value: typing.Any
    tasks = typing.Set[asyncio.Task]
    _operations: typing.Dict[str, typing.AsyncIterator]

    def __init__(self, ws, context_value=None):
        self.ws = ws
        self.context_value = context_value
        self._operations = {}
        self.tasks = set()

    def __getitem__(self, key: str) -> typing.AsyncIterator:
        return self._operations[key]

    def __setitem__(self, key: str, value: typing.AsyncIterator) -> None:
        self._operations[key] = value

    def __delitem__(self, key: str) -> None:
        del self._operations[key]

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self._operations)

    def __len__(self) -> int:
        return len(self._operations)

    @property
    @abc.abstractmethod
    def closed(self) -> bool:
        pass

    @abc.abstractmethod
    async def close(self, code: int) -> None:
        pass

    @abc.abstractmethod
    def receive(self) -> str:
        pass

    @abc.abstractmethod
    async def send(self, data: str) -> None:
        pass
