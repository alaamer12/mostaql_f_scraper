"""Bounded async channels used to stream milestone items between stages."""

import asyncio
from typing import Any, AsyncIterator, Iterable, Optional


class _Sentinel:
    """Marker object pushed onto a channel to signal end-of-stream."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<END_OF_STREAM>"


END_OF_STREAM = _Sentinel()


class Channel:
    """A bounded, single-writer/multi-reader stream of milestone items.

    Thin wrapper over :class:`asyncio.Queue` that adds an explicit
    end-of-stream sentinel plus the counters the live progress display
    needs (``depth``, ``sent``, ``received``).
    """

    def __init__(self, name: str = "", maxsize: int = 100):
        self.name = name
        self.maxsize = maxsize
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._sent = 0
        self._received = 0
        self._closed = False

    # -- producer side -------------------------------------------------
    async def send(self, item: Any) -> None:
        """Put an item on the channel, blocking while the queue is full."""
        if self._closed:
            raise RuntimeError(f"Channel {self.name!r} is closed")
        await self._queue.put(item)
        self._sent += 1

    async def send_many(self, items: Iterable[Any]) -> None:
        for item in items:
            await self.send(item)

    async def close(self) -> None:
        """Signal that no further items will be sent."""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(END_OF_STREAM)

    # -- consumer side -------------------------------------------------
    async def receive(self) -> Any:
        """Return the next item, or ``None`` once the stream is exhausted."""
        item = await self._queue.get()
        if item is END_OF_STREAM:
            # Keep the sentinel available for any other consumer.
            await self._queue.put(END_OF_STREAM)
            return None
        self._received += 1
        return item

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        while True:
            item = await self.receive()
            if item is None:
                return
            yield item

    # -- introspection -------------------------------------------------
    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def sent(self) -> int:
        return self._sent

    @property
    def received(self) -> int:
        return self._received

    @property
    def closed(self) -> bool:
        return self._closed


class NullChannel(Channel):
    """A channel that silently drops everything sent to it.

    Used as the output channel of the last stage in a chain (and of every
    stage when running a plain, non-pipelined command).
    """

    def __init__(self, name: str = "null"):
        super().__init__(name=name, maxsize=1)

    async def send(self, item: Any) -> None:  # noqa: D102
        self._sent += 1

    async def close(self) -> None:  # noqa: D102
        self._closed = True

    async def receive(self) -> Any:  # noqa: D102
        return None


class SeededChannel(Channel):
    """A channel pre-filled from disk, used by a stage in ``start`` position.

    Items are yielded from an in-memory iterable, so no producer task is
    needed and the queue bound does not apply.
    """

    def __init__(self, items: Iterable[Any], name: str = "seed"):
        super().__init__(name=name, maxsize=1)
        self._items = list(items)
        self._index = 0
        self._closed = True

    async def send(self, item: Any) -> None:  # noqa: D102
        raise RuntimeError("SeededChannel is read-only")

    async def close(self) -> None:  # noqa: D102
        self._closed = True

    async def receive(self) -> Optional[Any]:  # noqa: D102
        if self._index >= len(self._items):
            return None
        item = self._items[self._index]
        self._index += 1
        self._received += 1
        return item

    @property
    def depth(self) -> int:  # noqa: D102
        return max(0, len(self._items) - self._index)
