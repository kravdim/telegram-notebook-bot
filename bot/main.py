"""Точка входа: инициализация бота, LLM, scheduler, запуск polling."""

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.handlers import admin, callbacks, commands, messages, onboarding
from bot.llm.client import LLMClient
from bot.llm.context import clear_all as clear_context
from bot.llm.queue import LLMQueue
from bot.middleware import WhitelistMiddleware
from bot.scheduler.healthcheck import check_llm_health
from bot.scheduler.reminders import send_pending_reminders
from bot.scheduler.sweep import sweep_missed_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Запуск бота."""
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token":
        logger.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # LLM
    llm_client = LLMClient()
    llm_queue = LLMQueue()
    llm_queue.start()
    messages.init(llm_client, llm_queue)

    # Очистка контекста при старте
    clear_context()

    # --- Фоновые задачи ---

    async def _reminders_loop():
        """Основной контур: отправка напоминаний каждые 30 секунд."""
        while True:
            await asyncio.sleep(30)
            try:
                await send_pending_reminders(bot)
            except Exception as e:
                logger.error("Reminders loop error: %s", e)

    async def _sweep_loop():
        """Двойной контур: sweep пропущенных каждые 5 минут."""
        while True:
            await asyncio.sleep(300)
            try:
                await sweep_missed_reminders(bot)
            except Exception as e:
                logger.error("Sweep loop error: %s", e)

    async def _health_loop():
        """Health check main LLM каждые 5 минут."""
        while True:
            await asyncio.sleep(300)
            try:
                await check_llm_health(llm_client)
            except Exception as e:
                logger.error("Health check error: %s", e)

    asyncio.create_task(_reminders_loop())
    asyncio.create_task(_sweep_loop())
    asyncio.create_task(_health_loop())

    # Middleware
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())

    # Роутеры (порядок: onboarding, admin, commands, callbacks первыми; messages — последний)
    dp.include_router(onboarding.router)
    dp.include_router(admin.router)
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(messages.router)

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(_shutdown(dp, bot, llm_queue))
        )

    logger.info("Бот запускается...")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await llm_queue.stop()
        await bot.session.close()
        logger.info("Бот остановлен.")


async def _shutdown(dp: Dispatcher, bot: Bot, llm_queue: LLMQueue) -> None:
    """Graceful shutdown."""
    logger.info("Получен сигнал завершения, останавливаемся...")
    await llm_queue.stop()
    await dp.stop_polling()


if __name__ == "__main__":
    asyncio.run(main())
