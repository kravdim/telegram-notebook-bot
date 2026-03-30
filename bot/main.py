"""Точка входа: инициализация бота, LLM, scheduler, запуск polling."""

import asyncio
import logging
import os
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from bot.config import settings
from bot.handlers import (
    admin, callbacks, commands, evening_review,
    messages, onboarding, trip, voice,
)
from bot.llm.client import LLMClient
from bot.llm.context import clear_all as clear_context
from bot.llm.queue import LLMQueue
from bot.middleware import WhitelistMiddleware
from bot.scheduler.backup import run_backup
from bot.scheduler.chronometry import send_chronometry_prompts
from bot.scheduler.digest import send_digests
from bot.scheduler.healthcheck import check_llm_health
from bot.scheduler.log_rotation import rotate_llm_logs
from bot.scheduler.memoir import send_memoir_prompts
from bot.scheduler.reminders import send_pending_reminders
from bot.scheduler.task_reminders import send_task_reminders
from bot.scheduler.weekly_review import send_weekly_review
from bot.scheduler.reindex import reindex_missing_embeddings
from bot.scheduler.sweep import sweep_missed_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _init_embedding_client():
    """Инициализация embedding-клиента на основе конфига."""
    yaml_cfg = settings.yaml_config
    provider = yaml_cfg.get("embedding", {}).get("provider", "ollama")

    try:
        if provider == "ollama":
            from bot.embeddings.ollama import OllamaEmbeddingClient
            client = OllamaEmbeddingClient()
        else:
            from bot.embeddings.cloud import CloudEmbeddingClient
            client = CloudEmbeddingClient()

        from bot.embeddings import indexer
        from bot.scheduler import reindex
        indexer.init(client)
        reindex.init(client)
        logger.info("Embedding-клиент инициализирован: %s", provider)
        return client
    except Exception as e:
        logger.warning("Embedding-клиент не инициализирован: %s", e)
        return None


def _init_stt_client():
    """Инициализация STT-клиента на основе конфига."""
    yaml_cfg = settings.yaml_config
    provider = yaml_cfg.get("stt", {}).get("provider", "local_whisper")

    try:
        if provider == "local_whisper":
            from bot.stt.local_whisper import LocalWhisperClient
            client = LocalWhisperClient()
        else:
            from bot.stt.cloud_stt import CloudSTTClient
            client = CloudSTTClient()

        voice.init(client)
        logger.info("STT-клиент инициализирован: %s", provider)
        return client
    except Exception as e:
        logger.warning("STT-клиент не инициализирован: %s", e)
        return None


async def main() -> None:
    """Запуск бота."""
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token":
        logger.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    # Прокси для Telegram API (из env: ALL_PROXY или HTTPS_PROXY)
    proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY")
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = Dispatcher(storage=MemoryStorage())

    # LLM
    llm_client = LLMClient()
    llm_queue = LLMQueue()
    llm_queue.start()
    messages.init(llm_client, llm_queue)

    # Хронометраж
    from bot.handlers import chronometry as chrono_handler
    chrono_handler.init(llm_client, llm_queue)

    # Embedding
    _init_embedding_client()

    # STT
    _init_stt_client()

    # Очистка контекста при старте
    clear_context()

    # Меню команд в Telegram
    await bot.set_my_commands(
        [
            BotCommand(command="today", description="Задачи на сегодня"),
            BotCommand(command="tasks", description="Все открытые задачи"),
            BotCommand(command="frog", description="Лягушка дня"),
            BotCommand(command="done", description="Отметить задачу выполненной"),
            BotCommand(command="projects", description="Проекты (слоны)"),
            BotCommand(command="notes", description="Заметки"),
            BotCommand(command="memoir", description="Мемуарник"),
            BotCommand(command="chrono", description="Хронометраж"),
            BotCommand(command="focus", description="Режим фокуса"),
            BotCommand(command="trip", description="Командировка"),
            BotCommand(command="birthdays", description="Дни рождения"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="export", description="Экспорт данных"),
            BotCommand(command="settings", description="Настройки"),
            BotCommand(command="help", description="Справка"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

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

    async def _digest_loop():
        """Проверка и отправка дайджестов каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                await send_digests(bot)
            except Exception as e:
                logger.error("Digest loop error: %s", e)

    async def _memoir_loop():
        """Проверка и отправка вопросов мемуарника каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                await send_memoir_prompts(bot)
            except Exception as e:
                logger.error("Memoir loop error: %s", e)

    async def _chronometry_loop():
        """Хронометраж: проверка каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                await send_chronometry_prompts(bot)
            except Exception as e:
                logger.error("Chronometry loop error: %s", e)

    async def _task_reminders_loop():
        """Напоминание актуальных задач каждые 2 часа в рабочее время."""
        while True:
            await asyncio.sleep(60)
            try:
                await send_task_reminders(bot)
            except Exception as e:
                logger.error("Task reminders loop error: %s", e)

    async def _weekly_review_loop():
        """Еженедельный обзор: проверка каждую минуту (отправка в вс 21:00)."""
        while True:
            await asyncio.sleep(60)
            try:
                await send_weekly_review(bot)
            except Exception as e:
                logger.error("Weekly review loop error: %s", e)

    async def _maintenance_loop():
        """Обслуживание: бэкап, ротация логов, реиндекс — раз в час."""
        while True:
            await asyncio.sleep(3600)
            try:
                await reindex_missing_embeddings()
                await rotate_llm_logs()

                # Бэкап в 3:00
                import pendulum
                now = pendulum.now()
                backup_hour = settings.yaml_config.get("scheduler", {}).get("backup_hour", 3)
                if now.hour == backup_hour:
                    await run_backup()
            except Exception as e:
                logger.error("Maintenance loop error: %s", e)

    asyncio.create_task(_reminders_loop())
    asyncio.create_task(_sweep_loop())
    asyncio.create_task(_health_loop())
    asyncio.create_task(_digest_loop())
    asyncio.create_task(_memoir_loop())
    asyncio.create_task(_chronometry_loop())
    asyncio.create_task(_task_reminders_loop())
    asyncio.create_task(_weekly_review_loop())
    asyncio.create_task(_maintenance_loop())

    # Middleware
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())

    # Роутеры (порядок важен: onboarding, admin, commands, callbacks первыми; messages — последний)
    dp.include_router(onboarding.router)
    dp.include_router(admin.router)
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(evening_review.router)
    dp.include_router(trip.router)
    dp.include_router(voice.router)
    dp.include_router(messages.router)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
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
    await dp.stop_polling()


if __name__ == "__main__":
    asyncio.run(main())
