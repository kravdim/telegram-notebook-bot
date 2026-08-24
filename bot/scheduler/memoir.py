"""Планировщик мемуарника: вопрос ГСД + недельный/месячный ревью."""

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
from bot.services.delivery import DeliveryPartSpec, DeliveryResult, deliver_batch

logger = logging.getLogger(__name__)


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
                timeline = await _send_day_timeline(bot, user, tz, today)
                if not timeline.completed:
                    continue

                prompt = (
                    await _send_weekly_review(bot, user, tz, today)
                    if now.day_of_week == pendulum.SUNDAY
                    else await _send_prompt(bot, user, today)
                )
                if not prompt.completed:
                    continue
                if prompt.message_ids and prompt.message_ids[-1] is not None:
                    await _persist_memoir_state(user.telegram_id, prompt.message_ids[-1])

                if now.day == now.days_in_month:
                    monthly = await _send_monthly_review(bot, user, tz, today)
                    if not monthly.completed:
                        continue

                async with async_session() as session:
                    await claim_date_marker(
                        session, user.telegram_id, "memoir_asked_date", today
                    )
                logger.info("Мемуарник отправлен: %s", user.telegram_id)

        except Exception as e:
            logger.error(
                "Ошибка мемуарника для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


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


async def _send_weekly_review(bot: Bot, user, tz: str, today=None) -> DeliveryResult:
    """Отправить недельный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=7)

    text = format_weekly_review(entries)
    parts = [
        DeliveryPartSpec(user.telegram_id, part, parse_mode="HTML")
        for part in split_html_message(text)
    ]
    parts.append(
        DeliveryPartSpec(
            user.telegram_id,
            format_memoir_question(),
            parse_mode="HTML",
            reply_markup=build_memoir_keyboard(),
        )
    )
    today = today or pendulum.now(tz).date()
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:weekly:{user.telegram_id}:{today.isoformat()}",
        user_id=user.telegram_id,
        kind="memoir_weekly",
        parts=parts,
    )


async def _send_prompt(bot: Bot, user, today) -> DeliveryResult:
    return await deliver_batch(
        bot,
        delivery_key=f"memoir:prompt:{user.telegram_id}:{today.isoformat()}",
        user_id=user.telegram_id,
        kind="memoir_prompt",
        parts=[
            DeliveryPartSpec(
                user.telegram_id,
                format_memoir_question(),
                parse_mode="HTML",
                reply_markup=build_memoir_keyboard(),
            )
        ],
    )


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
