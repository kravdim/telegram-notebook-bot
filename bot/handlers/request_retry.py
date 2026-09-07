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
    await _show_retries(message, message.from_user.id, 0)


async def _show_retries(message: Message, user_id: int, offset: int) -> None:
    async with async_session() as session:
        requests = list((await session.scalars(select(ProcessedRequest).where(
            ProcessedRequest.user_id == user_id,
            or_(
                ProcessedRequest.status == "failed",
                and_(ProcessedRequest.status == "processing",
                     ProcessedRequest.created_at < pendulum.now("UTC").subtract(minutes=5)),
            ),
            ProcessedRequest.action_plan.is_not(None),
            ProcessedRequest.action_results["_abandoned"].is_(None),
        ).order_by(ProcessedRequest.created_at.desc(), ProcessedRequest.request_key)
          .offset(offset).limit(6))).all())
    if not requests:
        await message.answer("Нет незавершённых запросов для продолжения.")
        return
    for request in requests[:5]:
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Продолжить запрос", callback_data=retry_data(request.request_key))
        keyboard.button(text="Больше не продолжать", callback_data=retry_data(request.request_key).replace("reqretry:", "reqdrop:"))
        previews = []
        for action in (request.action_plan or [])[:3]:
            arguments = action.get("arguments", {})
            if isinstance(arguments, dict):
                preview = next((arguments.get(key) for key in
                                ("title", "search_query", "message", "content")
                                if isinstance(arguments.get(key), str)), None)
                if preview:
                    previews.append(preview[:160])
        await message.answer(
            f"Запрос от {request.created_at:%d.%m %H:%M UTC}: "
            f"сохранено действий {sum(k.isdigit() for k in request.action_results)} "
            f"из {len(request.action_plan or [])}. "
            "Продолжение не повторит уже сохранённое. Новый текст — отдельный запрос."
            + ("\n\n" + "\n".join(previews) if previews else ""),
            reply_markup=keyboard.as_markup(), parse_mode=None,
        )
    navigation = InlineKeyboardBuilder()
    if offset:
        navigation.button(text="← Новее", callback_data=f"reqpage:{max(0, offset - 5)}")
    if len(requests) > 5:
        navigation.button(text="Старее →", callback_data=f"reqpage:{offset + 5}")
    if offset or len(requests) > 5:
        await message.answer("Другие незавершённые запросы:", reply_markup=navigation.as_markup())


@router.callback_query(F.data.startswith("reqpage:"))
async def page_request_retries(callback: CallbackQuery) -> None:
    await callback.answer()
    value = callback_data(callback).partition(":")[2]
    if value.isdecimal() and len(value) <= 6:
        await _show_retries(callback_message(callback), callback.from_user.id, int(value))


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


@router.callback_query(F.data.startswith("reqdrop:"))
async def confirm_abandon(callback: CallbackQuery) -> None:
    await callback.answer()
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Да, закрыть продолжение", callback_data=callback_data(callback).replace("reqdrop:", "reqclose:", 1))
    await callback_message(callback).answer(
        "Уже сохранённые действия останутся. Закрыть только возможность продолжить этот запрос?",
        reply_markup=keyboard.as_markup(), parse_mode=None,
    )


@router.callback_query(F.data.startswith("reqclose:"))
async def abandon_request(callback: CallbackQuery) -> None:
    await callback.answer()
    encoded = callback_data(callback).partition(":")[2]
    try:
        key = base64.b64decode(encoded + "=", altchars=b"-_", validate=True).hex()
    except (ValueError, binascii.Error):
        return
    async with async_session() as session:
        row = await session.scalar(select(ProcessedRequest).where(
            ProcessedRequest.request_key == key,
            ProcessedRequest.user_id == callback.from_user.id,
        ).with_for_update())
        if row is None:
            return
        if row.status == "processing" and row.created_at > pendulum.now("UTC").subtract(minutes=5):
            await callback_message(callback).answer("Запрос ещё выполняется. Дождись результата.")
            return
        row.action_results = {**row.action_results, "_abandoned": {"schema_version": 1}}
        row.status = "completed"
        row.completed_at = pendulum.now("UTC")
        await session.commit()
    await callback_message(callback).edit_text(
        "Продолжение закрыто. Ранее сохранённые действия остались.", reply_markup=None,
    )
