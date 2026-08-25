"""Обработчик голосовых сообщений: STT → confirm → обработка."""

import asyncio
import logging
import tempfile
import time
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.application.interactions import WorkflowType, interaction_service
from bot.config import settings
from bot.handlers.telegram import callback_message, message_bot
from bot.observability import metrics
from bot.stt.base import STTClient

logger = logging.getLogger(__name__)

router = Router()

# Глобальные ссылки
_stt_client: STTClient | None = None

# Кэш транскрибации: user_id → текст (для подтверждения)
_pending_transcripts: dict[int, str] = {}
_awaiting_edit: set[int] = set()


def init(stt_client: STTClient) -> None:
    """Установить STT-клиент."""
    global _stt_client
    _stt_client = stt_client


def get_client() -> STTClient | None:
    """Return the initialized shared STT client for health checks."""
    return _stt_client


def consume_voice_edit(user_id: int) -> bool:
    """Вернуть True, если следующий текст является исправлением голосового."""
    if user_id not in _awaiting_edit:
        return False
    _awaiting_edit.discard(user_id)
    _pending_transcripts.pop(user_id, None)
    return True


async def _persist_voice_state(
    user_id: int,
    state_type: WorkflowType,
    payload: dict,
    *,
    expected_type: WorkflowType | None = None,
) -> bool:
    try:
        if expected_type:
            state = await interaction_service.transition(
                user_id,
                expected_type,
                state_type,
                payload,
                30,
            )
        else:
            state = await interaction_service.claim(
                user_id, state_type, payload, 30
            )
        return state is not None
    except Exception as e:
        logger.warning("Не удалось сохранить voice state: %s", e)
        return False


async def _load_voice_state(user_id: int, state_type: WorkflowType):
    try:
        return await interaction_service.get(user_id, state_type)
    except Exception as e:
        logger.debug("Не удалось прочитать voice state: %s", e)
    return None


async def _clear_voice_state(user_id: int) -> None:
    try:
        state = await interaction_service.get(user_id)
        if state and state.state_type in {"voice_confirm", "voice_edit"}:
            expected_type: WorkflowType = (
                "voice_confirm" if state.state_type == "voice_confirm" else "voice_edit"
            )
            await interaction_service.clear(user_id, expected_type)
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

    bot = message_bot(message)
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Скачиваем голосовое
    voice = message.voice
    if voice is None:
        await message.answer("В сообщении нет голосового файла.")
        return

    # Лимит размера: 20 MB
    max_voice_size = 20 * 1024 * 1024
    if voice.file_size and voice.file_size > max_voice_size:
        await message.answer("Голосовое слишком длинное (макс. 20 МБ). Попробуй короче.")
        return

    file = await bot.get_file(voice.file_id)
    if not file.file_path:
        await message.answer("Telegram не вернул путь к голосовому файлу.")
        return

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        await bot.download_file(file.file_path, tmp_path)

    await message.answer("🎤 Распознаю голосовое…")
    started = time.monotonic()
    try:
        timeout_sec = int(
            settings.yaml_config.get("stt", {}).get("timeout_sec", 90)
        )
        text = await asyncio.wait_for(
            _stt_client.transcribe(tmp_path), timeout=timeout_sec
        )
        metrics.increment("stt.success")
    except asyncio.TimeoutError:
        metrics.increment("stt.timeout")
        logger.error("STT timeout")
        await message.answer(
            "Распознавание заняло слишком много времени. Попробуй более короткое голосовое."
        )
        return
    except Exception as e:
        metrics.increment("stt.error")
        logger.error("Ошибка STT: %s", e)
        await message.answer("Не удалось распознать голосовое. Попробуй ещё раз.")
        return
    finally:
        elapsed = time.monotonic() - started
        metrics.observe("stt.transcription_seconds", elapsed)
        metrics.gauge("stt.last_transcription_seconds", elapsed)
        tmp_path.unlink(missing_ok=True)

    if not text or not text.strip():
        await message.answer("Не удалось распознать текст из голосового.")
        return

    # Сохраняем и показываем для подтверждения
    persisted = await _persist_voice_state(
        message.from_user.id,
        "voice_confirm",
        {"transcript": text},
    )
    if not persisted:
        await message.answer(
            "Сначала заверши текущий диалог с ботом, затем отправь голосовое снова."
        )
        return
    _pending_transcripts[message.from_user.id] = text

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
        await callback_message(callback).edit_text("Сессия истекла. Отправь голосовое ещё раз.")
        return

    await _clear_voice_state(user_id)

    message = callback_message(callback)
    await message.edit_text(
        f"🎤 {text}\n\n⏳ Выполняю подтверждённую команду…",
        parse_mode=None,
    )

    # Обрабатываем распознанный текст через LLM
    from bot.handlers.messages import process_text_message
    await process_text_message(user_id, text, message)


@router.callback_query(F.data == "voice_edit")
async def cb_voice_edit(callback: CallbackQuery) -> None:
    """Пользователь хочет исправить транскрибацию."""
    await callback.answer()
    await callback_message(callback).edit_text(
        "Введи исправленный текст — я обработаю его как обычное сообщение.",
        reply_markup=None,
    )
    persisted = await _persist_voice_state(
        callback.from_user.id,
        "voice_edit",
        {},
        expected_type="voice_confirm",
    )
    if persisted:
        _awaiting_edit.add(callback.from_user.id)
    else:
        await callback_message(callback).edit_text(
            "Сессия истекла. Отправь голосовое ещё раз.",
            reply_markup=None,
        )


@router.callback_query(F.data == "voice_cancel")
async def cb_voice_cancel(callback: CallbackQuery) -> None:
    """Отмена голосового."""
    await callback.answer()
    _pending_transcripts.pop(callback.from_user.id, None)
    _awaiting_edit.discard(callback.from_user.id)
    await _clear_voice_state(callback.from_user.id)
    await callback_message(callback).edit_text("❌ Голосовое отменено.", reply_markup=None)
