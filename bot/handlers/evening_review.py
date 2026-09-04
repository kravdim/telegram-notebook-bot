"""Обработчик вечернего разбора невыполненных задач."""

import html
import logging
import uuid as uuid_mod

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.engine import async_session
from bot.handlers.telegram import callback_data, callback_message
from bot.services.tasks import update_task_workflow as update_task

logger = logging.getLogger(__name__)

router = Router()


def build_review_keyboard(task_id: str) -> InlineKeyboardBuilder:
    """Клавиатура для разбора задачи."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Перенести на завтра", callback_data=f"review_tomorrow:{task_id}")
    kb.button(text="🗑 Отменить", callback_data=f"review_cancel:{task_id}")
    kb.button(text="⏳ Оставить", callback_data=f"review_keep:{task_id}")
    kb.adjust(2, 1)
    return kb


@router.callback_query(F.data.startswith("review_tomorrow:"))
async def cb_review_tomorrow(callback: CallbackQuery) -> None:
    """Перенести задачу на завтра."""
    task_id = uuid_mod.UUID(callback_data(callback).split(":", 1)[1])
    await callback.answer()

    import pendulum

    from bot.db.crud.users import get_user

    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
        tz = user.timezone if user else "Europe/Moscow"
        tomorrow = pendulum.tomorrow(tz).date()

        task = await update_task(
            session, task_id, callback.from_user.id, scheduled_date=tomorrow
        )

    if task:
        await callback_message(callback).edit_text(
            f"📅 «{html.escape(task.title)}» перенесена на завтра",
            reply_markup=None,
        )
    else:
        await callback_message(callback).edit_text("Задача не найдена.", reply_markup=None)


@router.callback_query(F.data.startswith("review_cancel:"))
async def cb_review_cancel(callback: CallbackQuery) -> None:
    """Отменить задачу."""
    task_id = uuid_mod.UUID(callback_data(callback).split(":", 1)[1])
    await callback.answer()

    async with async_session() as session:
        task = await update_task(
            session, task_id, callback.from_user.id,
            status="cancelled",
        )

    if task:
        await callback_message(callback).edit_text(
            f"🗑 «{html.escape(task.title)}» отменена",
            reply_markup=None,
        )


@router.callback_query(F.data.startswith("review_keep:"))
async def cb_review_keep(callback: CallbackQuery) -> None:
    """Оставить задачу как есть."""
    await callback.answer()
    await callback_message(callback).edit_text(
        "⏳ Оставлена без изменений.",
        reply_markup=None,
    )
