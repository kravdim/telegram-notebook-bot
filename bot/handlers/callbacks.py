"""Callback handlers: snooze напоминаний, confirm удаления задач."""

import html
import logging
import uuid
from datetime import timedelta

import pendulum
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.reminders import get_reminder_by_id, resolve_reminder, snooze_reminder
from bot.db.crud.tasks import complete_task_by_id, delete_task, get_task_by_id
from bot.db.crud.users import get_user
from bot.db.crud.projects import complete_project_and_cancel_open_tasks
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

router = Router()

MAX_SNOOZE = 5


# --- Snooze напоминаний ---

@router.callback_query(F.data.startswith("snooze_30:"))
async def cb_snooze_30(callback: CallbackQuery) -> None:
    """Отложить на 30 минут."""
    reminder_id = callback.data.split(":", 1)[1]
    await _do_snooze(callback, reminder_id, minutes=30)


@router.callback_query(F.data.startswith("snooze_60:"))
async def cb_snooze_60(callback: CallbackQuery) -> None:
    """Отложить на 1 час."""
    reminder_id = callback.data.split(":", 1)[1]
    await _do_snooze(callback, reminder_id, minutes=60)


@router.callback_query(F.data.startswith("snooze_morning:"))
async def cb_snooze_morning(callback: CallbackQuery) -> None:
    """Отложить до завтра утром."""
    reminder_id = callback.data.split(":", 1)[1]

    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
    tz = user.timezone if user else "Europe/Moscow"
    morning_time = user.digest_morning_time if user else None

    tomorrow = pendulum.tomorrow(tz)
    if morning_time:
        new_time = tomorrow.set(hour=morning_time.hour, minute=morning_time.minute)
    else:
        new_time = tomorrow.set(hour=8, minute=0)

    await _do_snooze(callback, reminder_id, absolute_time=new_time)


@router.callback_query(F.data.startswith("snooze_done:"))
async def cb_snooze_done(callback: CallbackQuery) -> None:
    """Напоминание выполнено — отметить задачу если есть."""
    reminder_id = callback.data.split(":", 1)[1]
    await callback.answer("✅")

    result_text = "✅ Готово!"
    try:
        async with async_session() as session:
            reminder = await get_reminder_by_id(
                session, uuid.UUID(reminder_id), callback.from_user.id
            )
            if reminder:
                await resolve_reminder(session, reminder.id, callback.from_user.id)
                if reminder.task_id:
                    task = await complete_task_by_id(session, reminder.task_id, callback.from_user.id)
                    if task:
                        result_text = f"✅ Задача «{html.escape(task.title)}» выполнена!"
    except Exception as e:
        logger.error("Ошибка при обработке snooze_done: %s", e)
        result_text = "✅ Готово!"

    try:
        await callback.message.edit_text(result_text, reply_markup=None)
    except TelegramBadRequest:
        pass  # Сообщение уже отредактировано (двойной клик)


async def _do_snooze(
    callback: CallbackQuery,
    reminder_id: str,
    minutes: int = 0,
    absolute_time=None,
) -> None:
    """Выполнить snooze."""
    await callback.answer()

    if absolute_time:
        new_time = absolute_time
    else:
        new_time = pendulum.now("UTC").add(minutes=minutes)

    try:
        async with async_session() as session:
            reminder = await snooze_reminder(
                session,
                uuid.UUID(reminder_id),
                new_time,
                callback.from_user.id,
            )

        if not reminder:
            await callback.message.edit_text("Напоминание не найдено.", reply_markup=None)
            return

        if reminder.snooze_count >= MAX_SNOOZE:
            await callback.message.edit_text(
                f"⚠️ Это напоминание уже откладывалось {reminder.snooze_count} раз.\n"
                f"Напоминание: {reminder.message}\n\n"
                "Может, пора взяться за это дело?",
                parse_mode=None,
                reply_markup=None,
            )
            return

        time_str = new_time.strftime("%d.%m %H:%M") if hasattr(new_time, 'strftime') else str(new_time)
        await callback.message.edit_text(
            f"⏰ Напомню: {time_str}\n{reminder.message}",
            parse_mode=None,
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass  # Сообщение уже отредактировано (двойной клик)


# --- Confirm удаления задач ---

@router.callback_query(F.data.startswith("task_delete_yes:"))
async def cb_delete_yes(callback: CallbackQuery) -> None:
    """Подтверждение удаления задачи."""
    task_id = callback.data.split(":", 1)[1]
    await callback.answer()

    async with async_session() as session:
        deleted = await delete_task(session, uuid.UUID(task_id), callback.from_user.id)

    if deleted:
        await callback.message.edit_text("🗑 Задача удалена.")
    else:
        await callback.message.edit_text("Не удалось удалить задачу.")


@router.callback_query(F.data.startswith("task_delete_no:"))
async def cb_delete_no(callback: CallbackQuery) -> None:
    """Отмена удаления задачи."""
    await callback.answer()
    await callback.message.edit_text("❌ Удаление отменено.")


def build_snooze_keyboard(reminder_id: str) -> InlineKeyboardBuilder:
    """Собрать inline-клавиатуру snooze для напоминания."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⏰ +30 мин", callback_data=f"snooze_30:{reminder_id}")
    kb.button(text="⏰ +1 час", callback_data=f"snooze_60:{reminder_id}")
    kb.button(text="📅 Завтра утром", callback_data=f"snooze_morning:{reminder_id}")
    kb.button(text="✅ Сделано", callback_data=f"snooze_done:{reminder_id}")
    kb.adjust(2, 2)
    return kb


def build_delete_confirm_keyboard(task_id: str) -> InlineKeyboardBuilder:
    """Собрать inline-клавиатуру подтверждения удаления."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"task_delete_yes:{task_id}")
    kb.button(text="❌ Отмена", callback_data=f"task_delete_no:{task_id}")
    kb.adjust(2)
    return kb


@router.callback_query(F.data.startswith("project_complete_yes:"))
async def cb_project_complete_yes(callback: CallbackQuery) -> None:
    """Закрыть проект и отменить оставшиеся открытые бифштексы."""
    project_id = callback.data.split(":", 1)[1]
    await callback.answer()
    async with async_session() as session:
        project = await complete_project_and_cancel_open_tasks(
            session, uuid.UUID(project_id), callback.from_user.id
        )
    text = (
        f"🐘 Слон «{project.title}» закрыт; оставшиеся задачи отменены ✅"
        if project else "Не удалось закрыть слона. Возможно, он уже закрыт."
    )
    await callback.message.edit_text(text, parse_mode=None, reply_markup=None)


@router.callback_query(F.data.startswith("project_complete_no:"))
async def cb_project_complete_no(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "Закрытие слона отменено. Сначала заверши или перенеси оставшиеся задачи.",
        reply_markup=None,
    )


def build_project_complete_keyboard(project_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Закрыть и отменить задачи",
        callback_data=f"project_complete_yes:{project_id}",
    )
    kb.button(text="Назад", callback_data=f"project_complete_no:{project_id}")
    kb.adjust(1)
    return kb
