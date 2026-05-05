"""Планировщик утренних и вечерних дайджестов."""

import logging

import pendulum
from aiogram import Bot

from bot.db.crud.birthdays import get_birthdays_on_date
from bot.db.crud.projects import get_project_progress, get_user_projects
from bot.db.crud.tasks import get_completed_today, get_frog, get_today_tasks, get_user_tasks
from bot.db.crud.trips import get_active_trip
from bot.db.crud.users import get_all_users, update_user_settings
from bot.db.engine import async_session
from bot.formatters import split_message
from bot.formatters.digest import format_evening_digest, format_morning_digest

logger = logging.getLogger(__name__)


async def send_digests(bot: Bot) -> None:
    """Проверить и отправить дайджесты всем пользователям."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        if not user.digest_enabled:
            continue

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)
            today = now.date()

            morning_time = user.digest_morning_time
            evening_time = user.digest_evening_time

            morning_target = now.set(
                hour=morning_time.hour, minute=morning_time.minute, second=0
            )
            evening_target = now.set(
                hour=evening_time.hour, minute=evening_time.minute, second=0
            )

            morning_sent, evening_sent = _digest_sent_flags(user, today)

            # Утренний дайджест (write-ahead: маркер до отправки)
            if now >= morning_target and not morning_sent:
                async with async_session() as session:
                    await update_user_settings(
                        session, user.telegram_id, digest_sent_date=today
                    )
                try:
                    await _send_morning(bot, user, today, tz)
                except Exception:
                    # Откатываем маркер при ошибке
                    async with async_session() as session:
                        await update_user_settings(
                            session, user.telegram_id, digest_sent_date=None
                        )
                    raise

            # Вечерний дайджест (write-ahead: маркер до отправки)
            if now >= evening_target and not evening_sent:
                async with async_session() as session:
                    await update_user_settings(
                        session, user.telegram_id, digest_evening_sent_date=today
                    )
                try:
                    await _send_evening(bot, user, today, tz)
                except Exception:
                    # Откатываем вечерний маркер при ошибке
                    async with async_session() as session:
                        await update_user_settings(
                            session, user.telegram_id, digest_evening_sent_date=None
                        )
                    raise

        except Exception as e:
            logger.error(
                "Ошибка дайджеста для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


async def _send_morning(bot: Bot, user, today, tz: str) -> None:
    """Отправить утренний дайджест."""
    is_weekend = today.weekday() >= 5

    async with async_session() as session:
        tasks = await get_today_tasks(session, user.telegram_id, today)
        frog = await get_frog(session, user.telegram_id)
        projects = await get_user_projects(session, user.telegram_id)
        trip = await get_active_trip(session, user.telegram_id)
        birthdays = await get_birthdays_on_date(session, user.telegram_id, today)

        project_progress = {}
        for p in projects[:3]:
            progress = await get_project_progress(session, p.id)
            project_progress[str(p.id)] = progress

    text = format_morning_digest(
        today=today,
        tasks=tasks,
        frog=frog,
        projects=projects,
        project_progress=project_progress,
        is_weekend=is_weekend,
        active_trip=trip.title if trip else None,
        birthdays=birthdays,
    )

    for part in split_message(text):
        await bot.send_message(
            chat_id=user.telegram_id, text=part, parse_mode="HTML"
        )
    logger.info("Утренний дайджест отправлен: %s", user.telegram_id)


def _digest_sent_flags(user, today) -> tuple[bool, bool]:
    """Вернуть, отправлены ли утренний и вечерний дайджесты за дату."""
    morning_sent = user.digest_sent_date is not None and user.digest_sent_date >= today
    evening_sent = (
        getattr(user, "digest_evening_sent_date", None) is not None
        and user.digest_evening_sent_date >= today
    )
    return morning_sent, evening_sent


async def _send_evening(bot: Bot, user, today, tz: str) -> None:
    """Отправить вечерний дайджест."""
    async with async_session() as session:
        all_tasks = await get_user_tasks(session, user.telegram_id, status="open")
        completed_today = await get_completed_today(session, user.telegram_id, today, tz)
        frog = await get_frog(session, user.telegram_id)

    # Оставшиеся на сегодня
    remaining = [
        t for t in all_tasks
        if (t.due_date and t.due_date <= today) or t.is_frog
    ]

    frog_done = frog is None or (frog and frog.status == "done")
    frog_title = frog.title if frog else None

    text = format_evening_digest(
        today=today,
        completed_tasks=completed_today,
        remaining_tasks=remaining,
        frog_done=frog_done,
        frog_title=frog_title,
    )

    for part in split_message(text):
        await bot.send_message(
            chat_id=user.telegram_id, text=part, parse_mode="HTML"
        )
    logger.info("Вечерний дайджест отправлен: %s", user.telegram_id)
