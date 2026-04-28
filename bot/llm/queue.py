"""Асинхронная очередь с приоритетами для LLM-запросов."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Приоритеты: меньше = важнее
PRIORITY_REMINDER = 1
PRIORITY_INTENT = 2
PRIORITY_CHRONOMETRY = 3
PRIORITY_DECOMPOSE = 4
PRIORITY_SUMMARIZE = 5


@dataclass(order=True)
class LLMTask:
    """Элемент очереди LLM."""
    priority: int
    coro: Coroutine = field(compare=False)
    future: asyncio.Future = field(compare=False)


class LLMQueue:
    """asyncio.PriorityQueue для LLM-запросов с одним воркером."""

    def __init__(self, maxsize: int = 100):
        self._queue: asyncio.PriorityQueue[LLMTask] = asyncio.PriorityQueue(maxsize=maxsize)
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        """Запустить воркер."""
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Остановить воркер."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def submit(self, priority: int, coro: Coroutine, timeout: float = 120.0) -> Any:
        """Добавить запрос в очередь и дождаться результата."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        task = LLMTask(priority=priority, coro=coro, future=future)
        await self._queue.put(task)
        return await asyncio.wait_for(future, timeout=timeout)

    async def _worker(self) -> None:
        """Обработка очереди последовательно."""
        while True:
            task = await self._queue.get()
            try:
                result = await task.coro
                if not task.future.done():
                    task.future.set_result(result)
            except Exception as e:
                if not task.future.done():
                    task.future.set_exception(e)
            finally:
                self._queue.task_done()
