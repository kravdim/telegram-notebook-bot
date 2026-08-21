"""Планировщик мемуарника: вопрос ГСД + недельный/месячный ревью."""

import logging

import pendulum
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.chronometry import get_today_entries
from bot.db.crud.memoir import get_memoir_entries
from bot.db.crud.users import claim_date_marker, get_all_users, release_date_marker
from bot.db.engine import async_session
from bot.formatters import split_html_message
from bot.formatters.chronometry import format_day_timeline
from bot.formatters.memoir import format_memoir_question, format_weekly_review

logger = logging.getLogger(__name__)

# user_id → message_id вопроса мемуарника (для отслеживания ответа)
_awaiting_memoir: dict[int, int] = {}


def is_awaiting_memoir(user_id: int) -> bool:
    """Проверить, ожидается ли ответ на мемуарник."""
    return user_id in _awaiting_memoir


def get_memoir_message_id(user_id: int) -> int | None:
    """Вернуть message_id вопроса мемуарника."""
    return _awaiting_memoir.get(user_id)


def clear_awaiting_memoir(user_id: int) -> None:
    """Очистить флаг ожидания ответа на мемуарник."""
    _awaiting_memoir.pop(user_id, None)


def build_memoir_keyboard():
    """Кнопка позволяет явно закрыть ожидание ответа."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data="memoir_skip")
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
                async with async_session() as session:
                    claimed = await claim_date_marker(
                        session, user.telegram_id, "memoir_asked_date", today
                    )
                if not claimed:
                    continue
                # Сначала — список дел дня по трекеру
                try:
                    await _send_day_timeline(bot, user, tz)

                # Воскресенье — недельный ревью
                    if now.day_of_week == pendulum.SUNDAY:
                        await _send_weekly_review(bot, user, tz)
                    else:
                        sent = await bot.send_message(
                            chat_id=user.telegram_id,
                            text=format_memoir_question(),
                            parse_mode="HTML",
                            reply_markup=build_memoir_keyboard(),
                        )
                        _awaiting_memoir[user.telegram_id] = sent.message_id
                        await _persist_memoir_state(user.telegram_id, sent.message_id)

                # Последний день месяца — месячный ревью
                    if now.day == now.days_in_month:
                        await _send_monthly_review(bot, user, tz)
                except Exception:
                    async with async_session() as session:
                        await release_date_marker(
                            session, user.telegram_id, "memoir_asked_date", today
                        )
                    raise
                logger.info("Мемуарник отправлен: %s", user.telegram_id)

        except Exception as e:
            logger.error(
                "Ошибка мемуарника для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


async def _send_day_timeline(bot: Bot, user, tz: str) -> None:
    """Отправить хронологический список занятий за день из трекера."""
    async with async_session() as session:
        entries = await get_today_entries(session, user.telegram_id, tz)
    if not entries:
        return
    text = format_day_timeline(entries, tz)
    for part in split_html_message(text):
        await bot.send_message(
            chat_id=user.telegram_id, text=part, parse_mode="HTML"
        )


async def _send_weekly_review(bot: Bot, user, tz: str) -> None:
    """Отправить недельный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=7)

    text = format_weekly_review(entries)
    for part in split_html_message(text):
        await bot.send_message(
            chat_id=user.telegram_id, text=part, parse_mode="HTML"
        )

    # Также задаём вопрос дня
    sent = await bot.send_message(
        chat_id=user.telegram_id,
        text=format_memoir_question(),
        parse_mode="HTML",
        reply_markup=build_memoir_keyboard(),
    )
    _awaiting_memoir[user.telegram_id] = sent.message_id
    await _persist_memoir_state(user.telegram_id, sent.message_id)


async def _persist_memoir_state(user_id: int, message_id: int) -> None:
    """Сохранить ожидание ответа так, чтобы оно пережило рестарт."""
    try:
        from bot.db.crud.interaction_states import set_state
        async with async_session() as session:
            await set_state(
                session,
                user_id,
                "memoir",
                payload={"message_id": message_id},
                ttl_minutes=60,
            )
    except Exception as e:
        logger.warning("Не удалось сохранить ожидание мемуарника: %s", e)


async def _send_monthly_review(bot: Bot, user, tz: str) -> None:
    """Отправить месячный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=31)

    if not entries:
        return

    values = {}
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

    await bot.send_message(
        chat_id=user.telegram_id,
        text="\n".join(parts),
        parse_mode="HTML",
    )
