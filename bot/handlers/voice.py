"""Обработчик голосовых сообщений: STT → confirm → обработка."""

import logging
import tempfile
from pathlib import Path
from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

router = Router()

# Глобальные ссылки
_stt_client = None

# Кэш транскрибации: user_id → текст (для подтверждения)
_pending_transcripts: dict[int, str] = {}


def init(stt_client) -> None:
    """Установить STT-клиент."""
    global _stt_client
    _stt_client = stt_client


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
    file = await message.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        await message.bot.download_file(file.file_path, tmp_path)

    try:
        text = await _stt_client.transcribe(tmp_path)
    except Exception as e:
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

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, верно", callback_data="voice_confirm")
    kb.button(text="✏️ Исправить", callback_data="voice_edit")
    kb.button(text="❌ Отмена", callback_data="voice_cancel")
    kb.adjust(2, 1)

    await message.answer(
        f"🎤 Распознано:\n\n<i>{text}</i>\n\nВсё верно?",
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
        await callback.message.edit_text("Сессия истекла. Отправь голосовое ещё раз.")
        return

    await callback.message.edit_text(f"🎤 {text}")

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
    # Текст остаётся в _pending_transcripts, но будет обработан как обычное сообщение


@router.callback_query(F.data == "voice_cancel")
async def cb_voice_cancel(callback: CallbackQuery) -> None:
    """Отмена голосового."""
    await callback.answer()
    _pending_transcripts.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Голосовое отменено.", reply_markup=None)
