"""Асинхронная очередь с приоритетами для LLM-запросов."""
from __future__ import annotations

import asyncio
import logging
import itertools
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
    sequence: int
    coro: Coroutine = field(compare=False)
    future: asyncio.Future = field(compare=False)
    execution_task: asyncio.Task | None = field(default=None, compare=False)


class LLMQueue:
    """asyncio.PriorityQueue для LLM-запросов с одним воркером."""

    def __init__(self, maxsize: int = 100):
        self._queue: asyncio.PriorityQueue[LLMTask] = asyncio.PriorityQueue(maxsize=maxsize)
        self._worker_task: asyncio.Task | None = None
        self._sequence = itertools.count()

    def start(self) -> None:
        """Запустить воркер."""
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Дождаться очереди и остановить воркер; при зависании отменить безопасно."""
        if self._worker_task:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("LLM queue did not drain in 30 seconds; cancelling worker")
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                pending.coro.close()
                if not pending.future.done():
                    pending.future.cancel()
                self._queue.task_done()
            self._worker_task = None

    async def submit(self, priority: int, coro: Coroutine, timeout: float = 120.0) -> Any:
        """Добавить запрос в очередь и дождаться результата."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        task = LLMTask(
            priority=priority,
            sequence=next(self._sequence),
            coro=coro,
            future=future,
        )
        await self._queue.put(task)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            if task.execution_task and not task.execution_task.done():
                task.execution_task.cancel()
            else:
                task.coro.close()
            raise

    async def _worker(self) -> None:
        """Обработка очереди последовательно."""
        while True:
            task = await self._queue.get()
            try:
                task.execution_task = asyncio.create_task(task.coro)
                result = await task.execution_task
                if not task.future.done():
                    task.future.set_result(result)
            except asyncio.CancelledError:
                if not task.future.cancelled():
                    raise
            except Exception as e:
                if not task.future.done():
                    task.future.set_exception(e)
            finally:
                self._queue.task_done()
