"""Tests for the streaming channels used by the --pipelined mode."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline.channel import Channel, NullChannel, SeededChannel  # noqa: E402


def test_send_close_and_iterate():
    async def scenario():
        ch = Channel("t", maxsize=10)
        await ch.send_many([1, 2, 3])
        await ch.close()
        return [item async for item in ch]

    assert asyncio.run(scenario()) == [1, 2, 3]


def test_counters_and_depth():
    async def scenario():
        ch = Channel("t", maxsize=10)
        await ch.send_many(["a", "b"])
        depth_before = ch.depth
        await ch.close()
        received = [item async for item in ch]
        return depth_before, ch.sent, ch.received, len(received)

    depth_before, sent, received, count = asyncio.run(scenario())
    assert depth_before == 2
    assert sent == 2
    assert received == 2
    assert count == 2


def test_close_is_idempotent_and_send_after_close_fails():
    async def scenario():
        ch = Channel("t", maxsize=2)
        await ch.close()
        await ch.close()
        try:
            await ch.send(1)
        except RuntimeError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_empty_stream_closes_cleanly():
    async def scenario():
        ch = Channel("t", maxsize=2)
        await ch.close()
        return [item async for item in ch]

    assert asyncio.run(scenario()) == []


def test_backpressure_blocks_producer():
    async def scenario():
        ch = Channel("t", maxsize=2)
        max_depth = 0

        async def producer():
            nonlocal max_depth
            for i in range(10):
                await ch.send(i)
                max_depth = max(max_depth, ch.depth)
            await ch.close()

        async def consumer():
            out = []
            async for item in ch:
                await asyncio.sleep(0)
                out.append(item)
            return out

        producer_task = asyncio.create_task(producer())
        items = await consumer()
        await producer_task
        return items, max_depth

    items, max_depth = asyncio.run(scenario())
    assert items == list(range(10))
    assert max_depth <= 2


def test_null_channel_drops_items():
    async def scenario():
        ch = NullChannel()
        await ch.send_many([1, 2, 3])
        await ch.close()
        return ch.sent, [item async for item in ch]

    sent, drained = asyncio.run(scenario())
    assert sent == 3
    assert drained == []


def test_seeded_channel_yields_preloaded_items():
    async def scenario():
        ch = SeededChannel([10, 20, 30])
        depth = ch.depth
        return depth, [item async for item in ch], ch.received

    depth, items, received = asyncio.run(scenario())
    assert depth == 3
    assert items == [10, 20, 30]
    assert received == 3
