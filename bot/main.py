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
from bot.observability import (
    alert_slo_violations,
    evaluate_slos,
    install_telegram_conflict_alert,
    observe_job,
)
from bot.runtime.readiness import RuntimeReadiness
from bot.runtime.singleton import SingletonLease
from bot.scheduler.backup import run_backup_if_due
from bot.scheduler.chronometry import send_chronometry_prompts
from bot.scheduler.digest import send_digests
from bot.scheduler.healthcheck import check_llm_health
from bot.scheduler.log_rotation import rotate_llm_logs
from bot.scheduler.memoir import send_memoir_prompts
from bot.scheduler.reindex import reindex_missing_embeddings
from bot.scheduler.reminders import send_pending_reminders
from bot.scheduler.sweep import sweep_missed_reminders
from bot.scheduler.task_reminders import send_task_reminders
from bot.scheduler.weekly_review import send_weekly_review
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

    from bot.application.interactions import interaction_service

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

    # --- Фоновые задачи ---

    async def _reminders_loop():
        """Основной контур: отправка напоминаний каждые 30 секунд."""
        while True:
            await asyncio.sleep(30)
            try:
                async with observe_job("reminders"):
                    await send_pending_reminders(bot)
            except Exception as e:
                logger.error("Reminders loop error: error_type=%s", error_type(e))

    async def _sweep_loop():
        """Двойной контур: sweep пропущенных каждые 5 минут."""
        while True:
            await asyncio.sleep(300)
            try:
                async with observe_job("reminder_sweep"):
                    await sweep_missed_reminders(bot)
            except Exception as e:
                logger.error("Sweep loop error: error_type=%s", error_type(e))

    async def _health_loop():
        """Health check main LLM каждые 5 минут."""
        while True:
            await asyncio.sleep(300)
            try:
                async with observe_job("health"):
                    await check_llm_health(llm_client)
                    slo_result = await evaluate_slos()
                    await alert_slo_violations(bot, slo_result)
            except Exception as e:
                logger.error("Health check error: error_type=%s", error_type(e))

    async def _digest_loop():
        """Проверка и отправка дайджестов каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                async with observe_job("digest"):
                    await send_digests(bot)
            except Exception as e:
                logger.error("Digest loop error: error_type=%s", error_type(e))

    async def _memoir_loop():
        """Проверка и отправка вопросов мемуарника каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                async with observe_job("memoir"):
                    await send_memoir_prompts(bot)
            except Exception as e:
                logger.error("Memoir loop error: error_type=%s", error_type(e))

    async def _chronometry_loop():
        """Хронометраж: проверка каждую минуту."""
        while True:
            await asyncio.sleep(60)
            try:
                async with observe_job("chronometry"):
                    await send_chronometry_prompts(bot)
            except Exception as e:
                logger.error("Chronometry loop error: error_type=%s", error_type(e))

    async def _task_reminders_loop():
        """Напоминание актуальных задач каждые 2 часа в рабочее время."""
        while True:
            await asyncio.sleep(60)
            try:
                async with observe_job("task_reminders"):
                    await send_task_reminders(bot)
            except Exception as e:
                logger.error("Task reminders loop error: error_type=%s", error_type(e))

    async def _weekly_review_loop():
        """Еженедельный обзор: проверка каждую минуту (отправка в вс 21:00)."""
        while True:
            await asyncio.sleep(60)
            try:
                async with observe_job("weekly_review"):
                    await send_weekly_review(bot)
            except Exception as e:
                logger.error("Weekly review loop error: error_type=%s", error_type(e))

    async def _maintenance_loop():
        """Обслуживание: бэкап, ротация логов, реиндекс — раз в час."""
        while True:
            try:
                async with observe_job("maintenance"):
                    await reindex_missing_embeddings()
                    await rotate_llm_logs()
                    await run_backup_if_due()
            except Exception as e:
                logger.error("Maintenance loop error: error_type=%s", error_type(e))
            await asyncio.sleep(3600)

    background_tasks = [
        asyncio.create_task(loop_fn(), name=loop_fn.__name__)
        for loop_fn in (
            _reminders_loop, _sweep_loop, _health_loop, _digest_loop,
            _memoir_loop, _chronometry_loop, _task_reminders_loop,
            _weekly_review_loop, _maintenance_loop,
        )
    ]
    if stt_client is not None:
        async def _warmup_stt() -> None:
            timeout_sec = int(
                settings.yaml_config.get("stt", {}).get("warmup_timeout_sec", 120)
            )
            try:
                ready = await asyncio.wait_for(
                    stt_client.health_check(), timeout=timeout_sec
                )
                if ready:
                    logger.info("STT-модель прогрета и готова")
                else:
                    logger.warning("STT-модель не прошла прогрев")
            except asyncio.TimeoutError:
                logger.warning("Прогрев STT превысил %s сек", timeout_sec)
            except Exception as exc:
                logger.warning("STT warmup failed: error_type=%s", error_type(exc))

        background_tasks.append(
            asyncio.create_task(_warmup_stt(), name="_warmup_stt")
        )

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
        if readiness is not None:
            await readiness.start()
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if readiness is not None:
            await readiness.stop()
        logging.getLogger("aiogram.dispatcher").removeHandler(conflict_handler)
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await llm_queue.stop()
        if stt_client is not None:
            try:
                await stt_client.close()
            except Exception as exc:
                logger.warning("STT cleanup failed: error_type=%s", error_type(exc))
        await bot.session.close()
        await singleton.release()
        await engine.dispose()
        logger.info("Бот остановлен.")


async def _shutdown(dp: Dispatcher, bot: Bot, llm_queue: LLMQueue) -> None:
    """Graceful shutdown."""
    logger.info("Получен сигнал завершения, останавливаемся...")
    await dp.stop_polling()


if __name__ == "__main__":
    asyncio.run(main())
