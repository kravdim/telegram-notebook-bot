"""Explicit owner-scoped retries use saved plans, never a newly interpreted message."""

import base64
import binascii

import pendulum
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import and_, or_, select

from bot.db.engine import async_session
from bot.db.models import ProcessedRequest
from bot.handlers.telegram import callback_data, callback_message

router = Router()


def retry_data(key: str) -> str:
    return "reqretry:" + base64.urlsafe_b64encode(bytes.fromhex(key)).decode().rstrip("=")


@router.message(Command("retry"))
async def list_request_retries(message: Message) -> None:
    if message.from_user is None:
        return
    async with async_session() as session:
        requests = list((await session.scalars(select(ProcessedRequest).where(
            ProcessedRequest.user_id == message.from_user.id,
            or_(
                ProcessedRequest.status == "failed",
                and_(ProcessedRequest.status == "processing",
                     ProcessedRequest.created_at < pendulum.now("UTC").subtract(minutes=5)),
            ),
            ProcessedRequest.action_plan.is_not(None),
        ).order_by(ProcessedRequest.created_at.desc()).limit(5))).all())
    if not requests:
        await message.answer("Нет незавершённых запросов для продолжения.")
        return
    for request in requests:
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Продолжить запрос", callback_data=retry_data(request.request_key))
        await message.answer(
            f"Запрос от {request.created_at:%d.%m %H:%M UTC}: "
            f"сохранено действий {sum(k.isdigit() for k in request.action_results)} "
            f"из {len(request.action_plan or [])}. "
            "Продолжение не повторит уже сохранённое. Новый текст — отдельный запрос.",
            reply_markup=keyboard.as_markup(), parse_mode=None,
        )


@router.callback_query(F.data.startswith("reqretry:"))
async def retry_request(callback: CallbackQuery) -> None:
    from bot.handlers.messages import process_text_message

    await callback.answer()
    encoded = callback_data(callback).partition(":")[2]
    try:
        key = base64.b64decode(encoded + "=", altchars=b"-_", validate=True).hex()
    except (ValueError, binascii.Error):
        return
    if len(key) != 64:
        return
    async with async_session() as session:
        plan = await session.scalar(select(ProcessedRequest.action_plan).where(
            ProcessedRequest.request_key == key,
            ProcessedRequest.user_id == callback.from_user.id,
        ))
    if plan is None:
        await callback_message(callback).answer("Этот запрос недоступен для продолжения.")
        return
    await process_text_message(
        callback.from_user.id, "", callback_message(callback), resume_key=key,
    )
