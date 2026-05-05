import asyncio

import pytest

from bot.llm.queue import LLMQueue


async def delayed(value, delay=0.0):
    await asyncio.sleep(delay)
    return value


@pytest.mark.asyncio
async def test_llm_queue_returns_result():
    queue = LLMQueue()
    queue.start()
    try:
        assert await queue.submit(2, delayed("ok")) == "ok"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_llm_queue_propagates_exceptions():
    async def boom():
        raise RuntimeError("failed")

    queue = LLMQueue()
    queue.start()
    try:
        with pytest.raises(RuntimeError, match="failed"):
            await queue.submit(2, boom())
    finally:
        await queue.stop()
