"""Readiness по успешному getUpdates, включая пустые long-poll ответы."""

import time

from aiogram.methods import GetUpdates

from bot.observability import metrics


class PollingHealth:
    def __init__(self) -> None:
        self.last_success: float | None = None
        self._last_monotonic: float | None = None

    @property
    def ready(self) -> bool:
        return self._last_monotonic is not None and time.monotonic() - self._last_monotonic < 90

    async def __call__(self, make_request, bot, method):
        try:
            result = await make_request(bot, method)
        except Exception:
            if isinstance(method, GetUpdates):
                metrics.increment("telegram.polling_error")
            raise
        if isinstance(method, GetUpdates):
            self.last_success = time.time()
            self._last_monotonic = time.monotonic()
            metrics.gauge("telegram.last_poll_success_epoch", self.last_success)
        return result
