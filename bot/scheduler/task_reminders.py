"""Периодическое напоминание актуальных задач в течение рабочего дня."""

import logging

import pendulum
from aiogram import Bot

from bot.db.crud.tasks import get_completed_today, get_frog, get_today_tasks
from bot.db.crud.users import get_all_users, update_user_settings
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

# Часы отправки (в локальном времени пользователя)
_REMINDER_HOURS = [9, 11, 13, 15, 17]


async def send_task_reminders(bot: Bot) -> None:
    """Проверить и отправить напоминания о задачах."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        if not user.digest_enabled:
            continue

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)
            today = now.date()
            current_hour = now.hour

            # Проверяем рабочий день
            if now.isoweekday() not in user.work_days:
                continue

            # Ищем подходящий слот: текущий час должен совпадать с одним из _REMINDER_HOURS
            # и мы в 5-минутном окне от начала часа
            if current_hour not in _REMINDER_HOURS:
                continue
            if now.minute > 5:
                continue

            # Идемпотентность: не отправляем дважды за один слот
            # tasks_reminder_last_hour хранит hour последней отправки (сбрасывается в 9)
            last_hour = user.tasks_reminder_last_hour
            if last_hour is not None and last_hour >= current_hour:
                # Уже отправляли в этот или более поздний слот сегодня
                # (сброс происходит автоматически: в 9 утра last_hour от вчера будет 17 > 9,
                #  но мы проверяем дату через digest_sent_date логику...
                #  Проще: если last_hour == 17 и сейчас 9 — нужно отправить)
                if not (last_hour == _REMINDER_HOURS[-1] and current_hour == _REMINDER_HOURS[0]):
                    continue

            # Получаем задачи
            async with async_session() as session:
                tasks = await get_today_tasks(session, user.telegram_id, today)
                completed = await get_completed_today(session, user.telegram_id, today, tz)
                frog = await get_frog(session, user.telegram_id)

            if not tasks and not completed:
                # Нет задач — не беспокоим
                continue

            text = _format_task_reminder(tasks, completed, frog, today, current_hour)

            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode="HTML",
            )

            # Обновляем маркер
            async with async_session() as session:
                await update_user_settings(
                    session, user.telegram_id,
                    tasks_reminder_last_hour=current_hour,
                )

            logger.info(
                "Напоминание задач (%d:00) отправлено: %s",
                current_hour, user.telegram_id,
            )

        except Exception as e:
            logger.error(
                "Ошибка напоминания задач для %s: %s",
                user.telegram_id, e, exc_info=True,
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
            lines.append(f"  ✓ <s>{t.title}</s>")
        if len(completed) > 3:
            lines.append(f"  ... и ещё {len(completed) - 3}")
        lines.append("")

    # Лягушка
    if frog:
        lines.append(f"🐸 Лягушка: <b>{frog.title}</b>")
        lines.append("")

    # Открытые задачи
    open_tasks = [t for t in tasks if not t.is_frog]
    if open_tasks:
        for t in open_tasks:
            icon = "🔴" if t.priority == "high" else "📌"
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
            overdue = ""
            if t.due_date and t.due_date < today:
                overdue = " ⚠️"
            lines.append(f"{icon} {t.title}{time_str}{overdue}")

    if not tasks and completed:
        lines.append("🎉 Все задачи выполнены! Отличная работа!")

    return "\n".join(lines)
