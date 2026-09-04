"""Планировщик мемуарника: вопрос ГСД + недельный/месячный ревью."""

import hashlib
import logging

import pendulum
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.chronometry import get_today_entries
from bot.db.crud.memoir import get_memoir_entries
from bot.db.crud.users import claim_date_marker, get_all_users
from bot.db.engine import async_session
from bot.formatters import split_html_message
from bot.formatters.chronometry import format_day_timeline
from bot.formatters.memoir import format_memoir_question, format_weekly_review
from bot.logging_safety import error_type
from bot.services.delivery import DeliveryPartSpec, DeliveryResult, deliver_batch
from bot.services.interactions import interaction_service

logger = logging.getLogger(__name__)


def build_memoir_keyboard(session_token: str):
    """Кнопка позволяет явно закрыть ожидание ответа."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data=f"memoir_skip:{session_token}")
    return kb.as_markup()


async def send_memoir_prompts(bot: Bot) -> None:
    """Проверить и отправить вопросы мемуарника."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)
            today = now.date()

            prompt_time = user.memoir_prompt_time
            target = now.set(hour=prompt_time.hour, minute=prompt_time.minute, second=0)

            # Идемпотентность
            if user.memoir_asked_date == today:
                continue

            if now >= target:
                timeline = await _send_day_timeline(bot, user, tz, today)
                if not timeline.completed:
                    continue

                if now.day == now.days_in_month:
                    monthly = await _send_monthly_review(bot, user, tz, today)
                    if not monthly.completed:
                        continue

                session_token = _memoir_session_token(user.telegram_id, today)
                if not await _claim_memoir_state(user.telegram_id, session_token):
                    continue

                prompt = (
                    await _send_weekly_review(bot, user, tz, today, session_token)
                    if now.day_of_week == pendulum.SUNDAY
                    else await _send_prompt(bot, user, today, session_token)
                )
                if not prompt.completed:
                    await _clear_memoir_state(user.telegram_id, session_token)
                    continue
                if not prompt.message_ids or prompt.message_ids[-1] is None:
                    await _clear_memoir_state(user.telegram_id, session_token)
                    continue
                if not await _persist_memoir_state(
                    user.telegram_id,
                    prompt.message_ids[-1],
                    session_token,
                    _memoir_reply_marker(today),
                ):
                    await _clear_memoir_state(user.telegram_id, session_token)
                    continue

                async with async_session() as session:
                    await claim_date_marker(
                        session, user.telegram_id, "memoir_asked_date", today
                    )
                logger.info("Мемуарник отправлен")

        except Exception as e:
            logger.error("Ошибка мемуарника: error_type=%s", error_type(e))


async def _send_day_timeline(bot: Bot, user, tz: str, today=None) -> DeliveryResult:
    """Отправить хронологический список занятий за день из трекера."""
    async with async_session() as session:
        entries = await get_today_entries(session, user.telegram_id, tz)
    if not entries:
        return DeliveryResult(completed=True)
    text = format_day_timeline(entries, tz)
    today = today or pendulum.now(tz).date()
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:timeline:{user.telegram_id}:{today.isoformat()}",
        user_id=user.telegram_id,
        kind="memoir_timeline",
        parts=[
            DeliveryPartSpec(user.telegram_id, part, parse_mode="HTML")
            for part in split_html_message(text)
        ],
    )


async def _send_weekly_review(
    bot: Bot, user, tz: str, today=None, session_token: str = "legacy"
) -> DeliveryResult:
    """Отправить недельный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=7)

    today = today or pendulum.now(tz).date()
    text = format_weekly_review(entries)
    parts = [
        DeliveryPartSpec(user.telegram_id, part, parse_mode="HTML")
        for part in split_html_message(text)
    ]
    parts.append(
        DeliveryPartSpec(
            user.telegram_id,
            format_memoir_question(today),
            parse_mode="HTML",
            reply_markup=build_memoir_keyboard(session_token),
        )
    )
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:weekly:{user.telegram_id}:{today.isoformat()}",
        user_id=user.telegram_id,
        kind="memoir_weekly",
        parts=parts,
    )


async def _send_prompt(bot: Bot, user, today, session_token: str) -> DeliveryResult:
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:prompt:{user.telegram_id}:{today.isoformat()}",
        user_id=user.telegram_id,
        kind="memoir_prompt",
        parts=[
            DeliveryPartSpec(
                user.telegram_id,
                format_memoir_question(today),
                parse_mode="HTML",
                reply_markup=build_memoir_keyboard(session_token),
            )
        ],
    )


def _memoir_session_token(user_id: int, day) -> str:
    raw = f"memoir:{user_id}:{day.isoformat()}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _memoir_reply_marker(day) -> str:
    """Return the visible prompt marker persisted for strict Reply ownership."""
    return f"Мемуарник · {day:%d.%m.%Y}"


async def _claim_memoir_state(user_id: int, session_token: str) -> bool:
    """Reserve the user's interaction slot before sending a memoir question."""
    try:
        state = await interaction_service.claim(
            user_id,
            "memoir",
            {"session_token": session_token, "phase": "reserved"},
            60,
        )
        return state is not None
    except Exception as e:
        logger.warning(
            "Не удалось сохранить ожидание мемуарника: error_type=%s",
            error_type(e),
        )
        return False


async def _persist_memoir_state(
    user_id: int,
    message_id: int,
    session_token: str,
    reply_marker: str | None = None,
) -> bool:
    """Attach the sent Telegram message to the reserved memoir state."""
    try:
        state = await interaction_service.transition(
            user_id,
            "memoir",
            "memoir",
            {
                "message_id": message_id,
                "session_token": session_token,
                "phase": "pending",
                **({"reply_marker": reply_marker} if reply_marker else {}),
            },
            60,
            session_token,
        )
        return state is not None
    except Exception as e:
        logger.warning(
            "Не удалось обновить ожидание мемуарника: error_type=%s",
            error_type(e),
        )
        return False


async def _clear_memoir_state(
    user_id: int, session_token: str | None = None
) -> None:
    try:
        await interaction_service.clear(user_id, "memoir", session_token)
    except Exception as e:
        logger.warning(
            "Не удалось освободить ожидание мемуарника: error_type=%s",
            error_type(e),
        )


async def _send_monthly_review(bot: Bot, user, tz: str, today=None) -> DeliveryResult:
    """Отправить месячный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=31)

    if not entries:
        return DeliveryResult(completed=True)

    values: dict[str, int] = {}
    for e in entries:
        tag = e.value_tag or "другое"
        values[tag] = values.get(tag, 0) + 1

    total = sum(values.values())
    parts = ["📔 <b>Мемуарник: итоги месяца</b>\n"]
    parts.append(f"Записей: {len(entries)}\n")
    parts.append("<b>Ценности:</b>")
    for v, cnt in sorted(values.items(), key=lambda x: -x[1]):
        pct = int(cnt / total * 100)
        parts.append(f"  {v}: {pct}% ({cnt})")

    today = today or pendulum.now(tz).date()
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:monthly:{user.telegram_id}:{today:%Y-%m}",
        user_id=user.telegram_id,
        kind="memoir_monthly",
        parts=[
            DeliveryPartSpec(
                user.telegram_id,
                "\n".join(parts),
                parse_mode="HTML",
            )
        ],
    )
