"""Owned lifecycle for DailyPlanner background jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot

from bot.config import settings
from bot.llm.client import LLMClient
from bot.logging_safety import error_type
from bot.observability import alert_slo_violations, evaluate_slos, observe_job
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

logger = logging.getLogger(__name__)

_AsyncAction = Callable[[], Awaitable[None]]


async def _run_periodic(
    name: str,
    interval_seconds: int,
    action: _AsyncAction,
    *,
    run_immediately: bool = False,
) -> None:
    """Run one supervised job forever without terminating sibling jobs."""
    first_run = True
    while True:
        if not (first_run and run_immediately):
            await asyncio.sleep(interval_seconds)
        first_run = False
        try:
            async with observe_job(name):
                await action()
        except Exception as exc:
            logger.error("%s loop error: error_type=%s", name, error_type(exc))


async def _health_action(bot: Bot, llm_client: LLMClient) -> None:
    await check_llm_health(llm_client)
    await alert_slo_violations(bot, await evaluate_slos())


async def _maintenance_action() -> None:
    await reindex_missing_embeddings()
    await rotate_llm_logs()
    await run_backup_if_due()


async def _warmup_stt(stt_client: STTClient) -> None:
    timeout_sec = int(settings.yaml_config.get("stt", {}).get("warmup_timeout_sec", 120))
    try:
        ready = await asyncio.wait_for(stt_client.health_check(), timeout=timeout_sec)
        if ready:
            logger.info("STT-модель прогрета и готова")
        else:
            logger.warning("STT-модель не прошла прогрев")
    except TimeoutError:
        logger.warning("Прогрев STT превысил %s сек", timeout_sec)
    except Exception as exc:
        logger.warning("STT warmup failed: error_type=%s", error_type(exc))


def start_background_tasks(
    bot: Bot,
    llm_client: LLMClient,
    stt_client: STTClient | None,
) -> list[asyncio.Task[None]]:
    """Create all runtime-owned jobs and return their cancellation handles."""
    jobs: tuple[tuple[str, int, _AsyncAction, bool], ...] = (
        ("reminders", 30, lambda: send_pending_reminders(bot), False),
        ("reminder_sweep", 300, lambda: sweep_missed_reminders(bot), False),
        ("health", 300, lambda: _health_action(bot, llm_client), False),
        ("digest", 60, lambda: send_digests(bot), False),
        ("memoir", 60, lambda: send_memoir_prompts(bot), False),
        ("chronometry", 60, lambda: send_chronometry_prompts(bot), False),
        ("task_reminders", 60, lambda: send_task_reminders(bot), False),
        ("weekly_review", 60, lambda: send_weekly_review(bot), False),
        ("maintenance", 3600, _maintenance_action, True),
    )
    tasks = [
        asyncio.create_task(
            _run_periodic(name, interval, action, run_immediately=immediate),
            name=f"background:{name}",
        )
        for name, interval, action, immediate in jobs
    ]
    if stt_client is not None:
        tasks.append(asyncio.create_task(_warmup_stt(stt_client), name="background:stt-warmup"))
    return tasks


async def stop_background_tasks(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel and join every runtime-owned background job."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
