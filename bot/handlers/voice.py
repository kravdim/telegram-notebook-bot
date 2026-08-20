"""Обработчик голосовых сообщений: STT → confirm → обработка."""

import logging
import tempfile
from html import escape
from pathlib import Path
from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.engine import async_session
from bot.observability import metrics

logger = logging.getLogger(__name__)

router = Router()

# Глобальные ссылки
_stt_client = None

# Кэш транскрибации: user_id → текст (для подтверждения)
_pending_transcripts: dict[int, str] = {}
_awaiting_edit: set[int] = set()


def init(stt_client) -> None:
    """Установить STT-клиент."""
    global _stt_client
    _stt_client = stt_client


def consume_voice_edit(user_id: int) -> bool:
    """Вернуть True, если следующий текст является исправлением голосового."""
    if user_id not in _awaiting_edit:
        return False
    _awaiting_edit.discard(user_id)
    _pending_transcripts.pop(user_id, None)
    return True


async def _persist_voice_state(user_id: int, state_type: str, payload: dict) -> None:
    try:
        from bot.db.crud.interaction_states import set_state
        async with async_session() as session:
            await set_state(session, user_id, state_type, payload=payload, ttl_minutes=30)
    except Exception as e:
        logger.debug("Не удалось сохранить voice state: %s", e)


async def _load_voice_state(user_id: int, state_type: str):
    try:
        from bot.db.crud.interaction_states import get_state
        async with async_session() as session:
            state = await get_state(session, user_id)
            if state and state.state_type == state_type:
                return state
    except Exception as e:
        logger.debug("Не удалось прочитать voice state: %s", e)
    return None


async def _clear_voice_state(user_id: int) -> None:
    try:
        from bot.db.crud.interaction_states import clear_state
        async with async_session() as session:
            await clear_state(session, user_id)
    except Exception as e:
        logger.debug("Не удалось очистить voice state: %s", e)


@router.message(F.voice)
async def handle_voice(message: Message) -> None:
    """Голосовое → STT → confirm."""
    if not message.from_user:
        return

    if not _stt_client:
        await message.answer("Распознавание голоса не настроено.")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Скачиваем голосовое
    voice = message.voice

    # Лимит размера: 20 MB
    max_voice_size = 20 * 1024 * 1024
    if voice.file_size and voice.file_size > max_voice_size:
        await message.answer("Голосовое слишком длинное (макс. 20 МБ). Попробуй короче.")
        return

    file = await message.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        await message.bot.download_file(file.file_path, tmp_path)

    try:
        text = await _stt_client.transcribe(tmp_path)
        metrics.increment("stt.success")
    except Exception as e:
        metrics.increment("stt.error")
        logger.error("Ошибка STT: %s", e)
        await message.answer("Не удалось распознать голосовое. Попробуй ещё раз.")
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    if not text or not text.strip():
        await message.answer("Не удалось распознать текст из голосового.")
        return

    # Сохраняем и показываем для подтверждения
    _pending_transcripts[message.from_user.id] = text
    await _persist_voice_state(
        message.from_user.id,
        "voice_confirm",
        {"transcript": text},
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, верно", callback_data="voice_confirm")
    kb.button(text="✏️ Исправить", callback_data="voice_edit")
    kb.button(text="❌ Отмена", callback_data="voice_cancel")
    kb.adjust(2, 1)

    await message.answer(
        f"🎤 Распознано:\n\n<i>{escape(text)}</i>\n\nВсё верно?",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "voice_confirm")
async def cb_voice_confirm(callback: CallbackQuery) -> None:
    """Подтвердить транскрибацию и обработать как текст."""
    await callback.answer()
    user_id = callback.from_user.id
    text = _pending_transcripts.pop(user_id, None)
    if not text:
        state = await _load_voice_state(user_id, "voice_confirm")
        if state:
            text = state.payload.get("transcript")

    if not text:
        await callback.message.edit_text("Сессия истекла. Отправь голосовое ещё раз.")
        return

    await _clear_voice_state(user_id)

    await callback.message.edit_text(f"🎤 {text}", parse_mode=None)

    # Обрабатываем распознанный текст через LLM
    from bot.handlers.messages import process_text_message
    await process_text_message(user_id, text, callback.message)


@router.callback_query(F.data == "voice_edit")
async def cb_voice_edit(callback: CallbackQuery) -> None:
    """Пользователь хочет исправить транскрибацию."""
    await callback.answer()
    await callback.message.edit_text(
        "Введи исправленный текст — я обработаю его как обычное сообщение.",
        reply_markup=None,
    )
    _awaiting_edit.add(callback.from_user.id)
    await _persist_voice_state(callback.from_user.id, "voice_edit", {})


@router.callback_query(F.data == "voice_cancel")
async def cb_voice_cancel(callback: CallbackQuery) -> None:
    """Отмена голосового."""
    await callback.answer()
    _pending_transcripts.pop(callback.from_user.id, None)
    _awaiting_edit.discard(callback.from_user.id)
    await _clear_voice_state(callback.from_user.id)
    await callback.message.edit_text("❌ Голосовое отменено.", reply_markup=None)
