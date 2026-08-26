"""Планировщик хронометража: периодический опрос в рабочее время."""

import asyncio
import logging
import random
import secrets

import pendulum
from aiogram import Bot

from bot.application.interactions import interaction_service
from bot.db.crud.users import get_all_users, update_user_settings
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

# user_id → message_id хронометражного вопроса (None = не ожидаем)
_awaiting_response: dict[int, int] = {}

# user_id → когда был задан текущий незакрытый вопрос
_awaiting_since: dict[int, pendulum.DateTime] = {}

# user_id → индекс последнего вопроса (чтобы не повторять подряд)
_last_question_idx: dict[int, int] = {}

# Один scheduler и admin-handler живут в одном event loop. Общий lock не даёт
# им одновременно отправить два вопроса одному пользователю.
_prompt_locks: dict[int, asyncio.Lock] = {}

_CHRONOMETRY_QUESTIONS = [
    "⏱ Чем занимаешься сейчас?",
    "⏱ Что делаешь в данный момент?",
    "⏱ На чём сейчас сфокусирован?",
    "⏱ Чем сейчас занят?",
    "⏱ Как проводишь время прямо сейчас?",
    "⏱ Что сейчас в работе?",
    "⏱ Расскажи, чем занят?",
    "⏱ Над чем сейчас работаешь?",
    "⏱ Что происходит прямо сейчас?",
    "⏱ Чему посвящаешь время сейчас?",
    "⏱ Как дела? Чем занят?",
    "⏱ Что на повестке прямо сейчас?",
]


def is_awaiting_response(user_id: int) -> bool:
    """Проверить, ожидается ли ответ на хронометраж от пользователя."""
    return user_id in _awaiting_response


def get_chrono_message_id(user_id: int) -> int | None:
    """Вернуть message_id хронометражного вопроса (или None)."""
    return _awaiting_response.get(user_id)


def clear_awaiting(user_id: int) -> None:
    """Очистить флаг ожидания ответа."""
    _awaiting_response.pop(user_id, None)
    _awaiting_since.pop(user_id, None)


async def send_chronometry_prompt_now(bot: Bot, user) -> str:
    """Ручной prompt вне расписания: sent, pending или busy."""
    tz = user.timezone or "Europe/Moscow"
    return await _send_prompt(bot, user, pendulum.now(tz))


async def _send_prompt(bot: Bot, user, now: pendulum.DateTime) -> str:
    """Отправить один вопрос с общей защитой scheduler/admin от дублей."""
    user_id = user.telegram_id
    lock = _prompt_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if user_id in _awaiting_response:
            return "pending"

        state = await interaction_service.get(user_id)
        if state:
            return "pending" if state.state_type == "chronometry" else "busy"

        session_token = secrets.token_urlsafe(8)
        claimed = await interaction_service.claim(
            user_id,
            "chronometry",
            {"session_token": session_token, "phase": "reserved"},
            max(user.chronometry_interval_min * 2, 60),
        )
        if not claimed:
            return "busy"

        last_idx = _last_question_idx.get(user_id, -1)
        available = [i for i in range(len(_CHRONOMETRY_QUESTIONS)) if i != last_idx]
        idx = random.choice(available)
        _last_question_idx[user_id] = idx

        try:
            sent = await bot.send_message(
                chat_id=user_id,
                text=_CHRONOMETRY_QUESTIONS[idx],
            )
        except Exception:
            await interaction_service.clear(user_id, "chronometry", session_token)
            raise
        _awaiting_response[user_id] = sent.message_id
        _awaiting_since[user_id] = now

        updated = await interaction_service.transition(
            user_id,
            "chronometry",
            "chronometry",
            {
                "message_id": sent.message_id,
                "session_token": session_token,
                "phase": "pending",
            },
            max(user.chronometry_interval_min * 2, 60),
            session_token,
        )
        if not updated:
            _awaiting_response.pop(user_id, None)
            _awaiting_since.pop(user_id, None)
            return "busy"

        async with async_session() as session:
            await update_user_settings(
                session, user_id,
                chronometry_last_asked=now,
            )
        logger.info("Хронометраж: вопрос отправлен %s", user_id)
        return "sent"


async def send_chronometry_prompts(bot: Bot) -> None:
    """Проверить и отправить вопросы хронометража."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        if not user.chronometry_enabled:
            continue

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)

            # Не беспокоим в нерабочее время (work_days: ISO 1=Пн...7=Вс)
            if now.isoweekday() not in user.work_days:
                continue

            current_time = now.time()
            if current_time < user.work_start_time or current_time > user.work_end_time:
                continue

            # Не перебиваем утренний дайджест и начало дня.
            morning_time = user.digest_morning_time
            morning_quiet_until = now.set(
                hour=morning_time.hour, minute=morning_time.minute, second=0
            ).add(minutes=10)
            work_start_quiet_until = now.set(
                hour=user.work_start_time.hour, minute=user.work_start_time.minute, second=0
            ).add(minutes=10)
            if now < max(morning_quiet_until, work_start_quiet_until):
                continue

            # Режим фокуса
            if user.focus_until:
                focus_until = pendulum.instance(user.focus_until)
                if now < focus_until:
                    continue

            if user.telegram_id in _awaiting_response:
                if not _awaiting_is_stale(
                    user.telegram_id, now, user.chronometry_interval_min
                ):
                    continue
                clear_awaiting(user.telegram_id)

            # Проверяем интервал (из БД, переживает рестарты)
            interval_sec = user.chronometry_interval_min * 60
            if user.chronometry_last_asked:
                last = pendulum.instance(user.chronometry_last_asked)
                if (now - last).in_seconds() < interval_sec:
                    continue

            await _send_prompt(bot, user, now)

        except Exception as e:
            logger.error(
                "Ошибка хронометража для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


def _awaiting_is_stale(user_id: int, now: pendulum.DateTime, interval_min: int) -> bool:
    """True, если старый незакрытый вопрос уже можно забыть."""
    asked_at = _awaiting_since.get(user_id)
    if not asked_at:
        return True
    stale_after = max(interval_min * 120, 3600)
    return (now - asked_at).in_seconds() >= stale_after
