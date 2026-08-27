"""Обработчик голосовых сообщений: STT → confirm → обработка."""

import asyncio
import logging
import secrets
import tempfile
import time
from html import escape
from pathlib import Path
from typing import cast

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.application.interactions import WorkflowType, interaction_service
from bot.config import settings
from bot.db.crud.users import get_user
from bot.db.engine import async_session
from bot.handlers.telegram import callback_data, callback_message, message_bot
from bot.logging_safety import error_type
from bot.observability import metrics
from bot.privacy import PRIVACY_NOTICE_VERSION, privacy_keyboard, privacy_notice_text
from bot.stt.base import STTClient

logger = logging.getLogger(__name__)

router = Router()

# Глобальные ссылки
_stt_client: STTClient | None = None

# Optional process-local caches. PostgreSQL session identity is always checked first.
_pending_transcripts: dict[tuple[int, str], str] = {}
_awaiting_edit: dict[int, str] = {}


def init(stt_client: STTClient) -> None:
    """Установить STT-клиент."""
    global _stt_client
    _stt_client = stt_client


def get_client() -> STTClient | None:
    """Return the initialized shared STT client for health checks."""
    return _stt_client


def _voice_keyboard(session_token: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, верно", callback_data=f"voice_confirm:{session_token}")
    kb.button(text="✏️ Исправить", callback_data=f"voice_edit:{session_token}")
    kb.button(text="❌ Отмена", callback_data=f"voice_cancel:{session_token}")
    kb.adjust(2, 1)
    return kb.as_markup()


def consume_voice_edit(user_id: int) -> bool:
    """Consume only the optional cache; persisted state remains authoritative."""
    session_token = _awaiting_edit.pop(user_id, None)
    if session_token is None:
        return False
    _pending_transcripts.pop((user_id, session_token), None)
    return True


async def _persist_voice_state(
    user_id: int,
    state_type: WorkflowType,
    payload: dict,
    *,
    expected_type: WorkflowType | None = None,
    expected_token: str | None = None,
) -> bool:
    try:
        if expected_type:
            state = await interaction_service.transition(
                user_id,
                expected_type,
                state_type,
                payload,
                30,
                expected_token,
            )
        else:
            state = await interaction_service.claim(
                user_id, state_type, payload, 30
            )
        return state is not None
    except Exception as e:
        logger.warning("Voice state save failed: error_type=%s", error_type(e))
        return False


async def _load_voice_state(user_id: int, state_type: WorkflowType):
    try:
        return await interaction_service.get(user_id, state_type)
    except Exception as e:
        logger.debug("Voice state load failed: error_type=%s", error_type(e))
    return None


async def _clear_voice_state(
    user_id: int,
    expected_type: WorkflowType | None = None,
    session_token: str | None = None,
) -> None:
    try:
        state = await interaction_service.get(user_id)
        if expected_type is not None:
            await interaction_service.clear(user_id, expected_type, session_token)
        elif state and state.state_type in {
            "voice_confirm", "voice_processing", "voice_edit"
        }:
            await interaction_service.clear(
                user_id, cast(WorkflowType, state.state_type)
            )
    except Exception as e:
        logger.debug("Voice state clear failed: error_type=%s", error_type(e))


@router.message(F.voice)
async def handle_voice(message: Message) -> None:
    """Голосовое → STT → confirm."""
    if not message.from_user:
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    if (
        user is None
        or getattr(user, "privacy_notice_version", 0) < PRIVACY_NOTICE_VERSION
        or not getattr(user, "cloud_processing_enabled", False)
    ):
        await message.answer(
            privacy_notice_text(
                enabled=getattr(user, "cloud_processing_enabled", None)
                if user
                else None
            ),
            parse_mode=None,
            reply_markup=privacy_keyboard(),
        )
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
        logger.error("STT failed: error_type=%s", error_type(e))
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
    session_token = secrets.token_urlsafe(8)
    state_payload = {
        "transcript": text,
        "session_token": session_token,
        "phase": "pending",
    }
    persisted = await _persist_voice_state(
        message.from_user.id,
        "voice_confirm",
        state_payload,
    )
    if not persisted:
        await message.answer(
            "Сначала заверши текущий диалог с ботом, затем отправь голосовое снова."
        )
        return
    _pending_transcripts[(message.from_user.id, session_token)] = text

    confirmation = await message.answer(
        f"🎤 Распознано:\n\n<i>{escape(text)}</i>\n\nВсё верно?",
        parse_mode="HTML",
        reply_markup=_voice_keyboard(session_token),
    )
    confirmation_id = getattr(confirmation, "message_id", None)
    if confirmation_id is not None:
        state_payload = {**state_payload, "message_id": confirmation_id}
        updated = await _persist_voice_state(
            message.from_user.id,
            "voice_confirm",
            state_payload,
            expected_type="voice_confirm",
            expected_token=session_token,
        )
        if not updated:
            _pending_transcripts.pop((message.from_user.id, session_token), None)


def _voice_callback_token(callback: CallbackQuery, action: str) -> str | None:
    prefix = f"{action}:"
    data = callback_data(callback)
    token = data[len(prefix):] if data.startswith(prefix) else ""
    return token or None


async def _matching_voice_state(
    callback: CallbackQuery, action: str, expected_type: WorkflowType
):
    token = _voice_callback_token(callback, action)
    if token is None:
        return None, None
    state = await _load_voice_state(callback.from_user.id, expected_type)
    message_id = getattr(callback_message(callback), "message_id", None)
    if (
        not state
        or state.payload.get("session_token") != token
        or state.payload.get("message_id") != message_id
    ):
        return None, token
    return state, token


async def _expire_stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Эта сессия уже устарела")
    await callback_message(callback).edit_text(
        "Сессия истекла. Отправь голосовое ещё раз.", reply_markup=None
    )


@router.callback_query(F.data.startswith("voice_confirm"))
async def cb_voice_confirm(callback: CallbackQuery) -> None:
    """Подтвердить транскрибацию и обработать как текст."""
    user_id = callback.from_user.id
    state, session_token = await _matching_voice_state(
        callback, "voice_confirm", "voice_confirm"
    )
    if not state or not session_token:
        await _expire_stale_callback(callback)
        return
    text = state.payload.get("transcript")
    if not isinstance(text, str) or not text.strip():
        await _expire_stale_callback(callback)
        return
    processing_payload = {**state.payload, "phase": "processing"}
    claimed = await _persist_voice_state(
        user_id,
        "voice_processing",
        processing_payload,
        expected_type="voice_confirm",
        expected_token=session_token,
    )
    if not claimed:
        await _expire_stale_callback(callback)
        return
    await callback.answer()

    message = callback_message(callback)
    await message.edit_text(
        f"🎤 {text}\n\n⏳ Выполняю подтверждённую команду…",
        parse_mode=None,
    )

    # Обрабатываем распознанный текст через LLM
    from bot.handlers.messages import MessageOutcome, process_text_message
    try:
        outcome = await process_text_message(user_id, text, message)
    except Exception as e:
        logger.error(
            "Voice command failed; confirmation remains retryable: error_type=%s",
            error_type(e),
        )
        outcome = MessageOutcome.RETRYABLE_ERROR
    if outcome != MessageOutcome.COMPLETED:
        retry_payload = {**state.payload, "phase": "failed"}
        restored = await _persist_voice_state(
            user_id,
            "voice_confirm",
            retry_payload,
            expected_type="voice_processing",
            expected_token=session_token,
        )
        if not restored:
            await message.edit_text(
                "Сессия истекла. Отправь голосовое ещё раз.", reply_markup=None
            )
            return
        await message.edit_text(
            f"🎤 {text}\n\nНе удалось выполнить команду. Можно повторить.",
            parse_mode=None,
            reply_markup=_voice_keyboard(session_token),
        )
        return
    _pending_transcripts.pop((user_id, session_token), None)
    await _clear_voice_state(user_id, "voice_processing", session_token)


@router.callback_query(F.data.startswith("voice_edit"))
async def cb_voice_edit(callback: CallbackQuery) -> None:
    """Пользователь хочет исправить транскрибацию."""
    state, session_token = await _matching_voice_state(
        callback, "voice_edit", "voice_confirm"
    )
    if not state or not session_token:
        await _expire_stale_callback(callback)
        return
    persisted = await _persist_voice_state(
        callback.from_user.id,
        "voice_edit",
        {**state.payload, "phase": "pending_edit"},
        expected_type="voice_confirm",
        expected_token=session_token,
    )
    if not persisted:
        await _expire_stale_callback(callback)
        return
    await callback.answer()
    await callback_message(callback).edit_text(
        "Введи исправленный текст — я обработаю его как обычное сообщение.",
        reply_markup=None,
    )
    _awaiting_edit[callback.from_user.id] = session_token


@router.callback_query(F.data.startswith("voice_cancel"))
async def cb_voice_cancel(callback: CallbackQuery) -> None:
    """Отмена голосового."""
    state, session_token = await _matching_voice_state(
        callback, "voice_cancel", "voice_confirm"
    )
    if not state or not session_token:
        await _expire_stale_callback(callback)
        return
    await callback.answer()
    _pending_transcripts.pop((callback.from_user.id, session_token), None)
    _awaiting_edit.pop(callback.from_user.id, None)
    await _clear_voice_state(
        callback.from_user.id, "voice_confirm", session_token
    )
    await callback_message(callback).edit_text("❌ Голосовое отменено.", reply_markup=None)
