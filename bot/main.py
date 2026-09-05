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
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from bot.config import settings
from bot.db.engine import engine
from bot.db.fsm_storage import DatabaseFSMStorage
from bot.embeddings.base import EmbeddingClient
from bot.handlers import (
    admin,
    callbacks,
    commands,
    evening_review,
    messages,
    onboarding,
    privacy,
    trip,
    voice,
)
from bot.llm.client import LLMClient
from bot.llm.context import clear_all as clear_context
from bot.llm.queue import LLMQueue
from bot.logging_safety import error_type
from bot.middleware import PrivateChatMiddleware, RateLimitMiddleware, WhitelistMiddleware
from bot.observability import install_telegram_conflict_alert
from bot.runtime.background import start_background_tasks, stop_background_tasks
from bot.runtime.readiness import RuntimeReadiness
from bot.runtime.singleton import SingletonLease
from bot.stt.base import STTClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _tmux_runtime_disallowed() -> bool:
    """DailyPlanner штатно принадлежит launchd/systemd, не tmux recovery."""
    return bool(os.environ.get("TMUX")) and os.environ.get(
        "DAILYPLANNER_ALLOW_TMUX"
    ) != "1"


def _init_embedding_client():
    """Инициализация embedding-клиента на основе конфига."""
    yaml_cfg = settings.yaml_config
    provider = yaml_cfg.get("embedding", {}).get("provider", "ollama")
    if provider == "disabled":
        logger.info("Embedding отключён выбранным runtime-профилем")
        return None

    try:
        client: EmbeddingClient
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
        logger.warning("Embedding client initialization failed: error_type=%s", error_type(e))
        return None


def _init_stt_client():
    """Инициализация STT-клиента на основе конфига."""
    yaml_cfg = settings.yaml_config
    provider = yaml_cfg.get("stt", {}).get("provider", "local_whisper")
    if provider == "disabled":
        logger.info("STT отключён выбранным runtime-профилем")
        return None

    try:
        client: STTClient
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
        logger.warning("STT client initialization failed: error_type=%s", error_type(e))
        return None


async def _cleanup_runtime_resources(
    singleton: SingletonLease,
    bot: Bot,
    llm_queue: LLMQueue,
    stt_client: STTClient | None,
) -> None:
    """Release every acquired runtime resource, including partial startup."""
    cleanups = [("LLM queue", llm_queue.stop)]
    if stt_client is not None:
        cleanups.append(("STT client", stt_client.close))
    cleanups.extend(
        [
            ("Telegram session", bot.session.close),
            ("singleton lease", singleton.release),
            ("database engine", engine.dispose),
        ]
    )
    for resource, cleanup in cleanups:
        try:
            await cleanup()
        except Exception as exc:
            logger.warning(
                "Runtime cleanup failed: resource=%s error_type=%s",
                resource,
                error_type(exc),
            )


async def main() -> None:
    """Запуск бота."""
    if _tmux_runtime_disallowed():
        logger.error(
            "Запуск DailyPlanner внутри tmux запрещён: используйте штатный "
            "LaunchAgent/systemd (override: DAILYPLANNER_ALLOW_TMUX=1)."
        )
        return
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token":
        logger.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)
    if not settings.access_control_configured:
        logger.error(
            "Доступ не настроен: добавьте allowed_telegram_ids/admin_telegram_ids "
            "или явно установите ALLOW_ALL_USERS=true для development."
        )
        sys.exit(1)
    config_errors = settings.runtime_config_errors()
    if config_errors:
        logger.error("Некорректная конфигурация: %s", "; ".join(config_errors))
        sys.exit(1)

    singleton = SingletonLease(engine)
    if not await singleton.acquire():
        logger.error(
            "Другой экземпляр DailyPlanner уже активен; запуск отменён до polling."
        )
        await engine.dispose()
        return

    from bot.services.interactions import interaction_service

    recovered_interactions = await interaction_service.recover_interrupted()
    if recovered_interactions:
        logger.warning(
            "Recovered interrupted interaction states: %d",
            recovered_interactions,
        )

    readiness_file = os.environ.get("READINESS_FILE")
    readiness = RuntimeReadiness(readiness_file) if readiness_file else None

    # Прокси для Telegram API (из env: ALL_PROXY или HTTPS_PROXY)
    proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY")
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = Dispatcher(storage=DatabaseFSMStorage())

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
    stt_client = _init_stt_client()

    # Очистка контекста при старте
    clear_context()

    # Меню команд в Telegram
    try:
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
                BotCommand(command="privacy", description="Privacy и cloud AI"),
                BotCommand(command="delete_data", description="Удаление всех данных"),
                BotCommand(command="settings", description="Настройки"),
                BotCommand(command="help", description="Справка"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except BaseException:
        await _cleanup_runtime_resources(singleton, bot, llm_queue, stt_client)
        raise

    background_tasks = start_background_tasks(bot, llm_client, stt_client)

    # Transport privacy is the outer fail-closed boundary for every handler.
    dp.message.middleware(PrivateChatMiddleware())
    dp.callback_query.middleware(PrivateChatMiddleware())
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Роутеры (порядок важен: onboarding, admin, commands, callbacks первыми; messages — последний)
    dp.include_router(onboarding.router)
    dp.include_router(privacy.router)
    dp.include_router(admin.router)
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(evening_review.router)
    dp.include_router(trip.router)
    dp.include_router(voice.router)
    from bot.handlers import request_retry

    dp.include_router(request_retry.router)
    dp.include_router(messages.router)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(_shutdown(dp, bot, llm_queue))
        )

    logger.info("Бот запускается...")
    conflict_handler = install_telegram_conflict_alert(bot, loop)

    try:
        polling_task = asyncio.create_task(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            name="telegram-polling",
        )
        await asyncio.sleep(2)
        if polling_task.done():
            await polling_task
        if readiness is not None:
            await readiness.start()
        await polling_task
    finally:
        if readiness is not None:
            await readiness.stop()
        logging.getLogger("aiogram.dispatcher").removeHandler(conflict_handler)
        await stop_background_tasks(background_tasks)
        await _cleanup_runtime_resources(singleton, bot, llm_queue, stt_client)
        logger.info("Бот остановлен.")


async def _shutdown(dp: Dispatcher, bot: Bot, llm_queue: LLMQueue) -> None:
    """Graceful shutdown."""
    logger.info("Получен сигнал завершения, останавливаемся...")
    await dp.stop_polling()


if __name__ == "__main__":
    asyncio.run(main())
