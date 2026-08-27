"""Периодическое напоминание актуальных задач в течение рабочего дня."""

import logging
from html import escape

import pendulum
from aiogram import Bot

from bot.db.crud.tasks import get_completed_today, get_frog, get_today_tasks
from bot.db.crud.users import (
    claim_task_reminder_slot,
    get_all_users,
    release_task_reminder_slot,
)
from bot.db.engine import async_session
from bot.formatters import split_html_message
from bot.logging_safety import error_type

logger = logging.getLogger(__name__)

# Часы отправки (в локальном времени пользователя)
_REMINDER_HOURS = [11, 13, 15, 17]


async def send_task_reminders(bot: Bot) -> None:
    """Проверить и отправить напоминания о задачах."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        # Напоминания задач не привязаны к digest_enabled — это отдельная фича
        # Пропускаем пользователей без задач (проверка ниже)

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)
            today = now.date()
            current_hour = now.hour

            # Проверяем рабочий день
            if now.isoweekday() not in user.work_days:
                continue

            # Весь текущий час относится к слоту: краткий простой приложения
            # не должен навсегда терять периодическое напоминание.
            if current_hour not in _REMINDER_HOURS:
                continue

            # Идемпотентность: не отправляем дважды за один слот в пределах даты.
            if _task_reminder_already_sent(user, today, current_hour):
                continue

            # Получаем задачи
            async with async_session() as session:
                tasks = await get_today_tasks(session, user.telegram_id, today)
                completed = await get_completed_today(session, user.telegram_id, today, tz)
                frog = await get_frog(session, user.telegram_id)

            if not tasks:
                # Нет открытых задач — не беспокоим периодическим списком.
                continue

            async with async_session() as session:
                claimed = await claim_task_reminder_slot(
                    session, user.telegram_id, today, current_hour
                )
            if not claimed:
                continue

            text = _format_task_reminder(tasks, completed, frog, today, current_hour)

            try:
                for part in split_html_message(text):
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=part,
                        parse_mode="HTML",
                    )
            except Exception:
                async with async_session() as session:
                    await release_task_reminder_slot(
                        session, user.telegram_id, today, current_hour
                    )
                raise

            logger.info(
                "Напоминание задач (%d:00) отправлено",
                current_hour,
            )

        except Exception as e:
            logger.error(
                "Ошибка напоминания задач: error_type=%s",
                error_type(e),
            )


def _format_task_reminder(tasks, completed, frog, today, hour) -> str:
    """Форматирование напоминания о задачах."""
    lines = []

    # Заголовок зависит от времени дня
    if hour <= 9:
        lines.append("🌅 <b>Доброе утро! Вот план на сегодня:</b>\n")
    elif hour <= 13:
        lines.append("☀️ <b>Актуальные задачи:</b>\n")
    elif hour <= 15:
        lines.append("🕐 <b>Середина дня. Что осталось:</b>\n")
    else:
        lines.append("🌇 <b>Финишная прямая! Осталось:</b>\n")

    # Выполненные сегодня
    if completed:
        lines.append(f"✅ Уже сделано: {len(completed)}")
        for t in completed[:3]:
            lines.append(f"  ✓ <s>{escape(t.title)}</s>")
        if len(completed) > 3:
            lines.append(f"  ... и ещё {len(completed) - 3}")
        lines.append("")

    # Лягушка
    if frog:
        lines.append(f"🐸 Лягушка: <b>{escape(frog.title)}</b>")
        lines.append("")

    # Открытые задачи
    open_tasks = [t for t in tasks if not t.is_frog]
    if open_tasks:
        for t in open_tasks:
            icon = "🔴" if t.priority == "high" else "📌"
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
            overdue = ""
            plan_date = getattr(t, "scheduled_date", None) or t.due_date
            if plan_date and plan_date < today:
                overdue = " ⚠️"
            lines.append(f"{icon} {escape(t.title)}{time_str}{overdue}")

    if not tasks and completed:
        lines.append("🎉 Все задачи выполнены! Отличная работа!")

    return "\n".join(lines)


def _task_reminder_already_sent(user, today, current_hour: int) -> bool:
    """Проверить, отправляли ли слот задач за текущую дату."""
    last_hour = user.tasks_reminder_last_hour
    last_date = getattr(user, "tasks_reminder_last_date", None)
    return last_date == today and last_hour is not None and last_hour >= current_hour
