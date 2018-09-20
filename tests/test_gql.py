import asyncio
from graphql.subscription.map_async_iterator import MapAsyncIterator
import pytest


@pytest.mark.asyncio
async def test():
    queue = asyncio.Queue()
    agen_closed = False

    async def make_agen():
        try:
            while True:
                yield await queue.get()
        finally:
            nonlocal agen_closed
            agen_closed = True

    agen = make_agen()
    mai = MapAsyncIterator(agen, lambda v: v)

    received = []

    async def run():
        async for i in mai:
            received.append(i)

    task = asyncio.ensure_future(run())
    await asyncio.sleep(.01)

    for i in range(5):
        await queue.put(i)
        await asyncio.sleep(.01)

    assert received == list(range(5))

    await mai.aclose()
    await asyncio.sleep(.01)
    assert agen_closed
    assert task.done()
