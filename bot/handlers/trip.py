"""Обработчик командировок: /trip on, /trip off."""

import logging

import pendulum
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db.crud.trips import complete_trip, create_trip, get_active_trip
from bot.db.crud.users import get_user
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("trip"))
async def cmd_trip(message: Message, command: CommandObject) -> None:
    """Управление командировками: /trip on [даты] [город], /trip off."""
    if not message.from_user:
        return

    args = command.args.strip() if command.args else ""

    if not args:
        # Показать текущую командировку
        async with async_session() as session:
            trip = await get_active_trip(session, message.from_user.id)

        if trip:
            await message.answer(
                f"✈️ <b>Командировка:</b> {trip.title}\n"
                f"📍 {trip.destination or '—'}\n"
                f"📅 {trip.start_date.strftime('%d.%m')} — {trip.end_date.strftime('%d.%m')}",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "Нет активной командировки.\n"
                "Используй: /trip on Командировка в Москву 20.03-25.03"
            )
        return

    if args.lower().startswith("off"):
        await _trip_off(message)
        return

    if args.lower().startswith("on"):
        await _trip_on(message, args[2:].strip())
        return

    await message.answer(
        "Используй:\n"
        "/trip — текущая командировка\n"
        "/trip on Название 20.03-25.03 Город\n"
        "/trip off — завершить"
    )


async def _trip_on(message: Message, text: str) -> None:
    """Создать командировку."""
    if not text:
        await message.answer("Укажи название и даты: /trip on Москва 20.03-25.03")
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    tz = user.timezone if user else "Europe/Moscow"

    # Парсим текст
    parts = text.split()
    title = text
    destination = None
    start_date = pendulum.now(tz).date()
    end_date = pendulum.now(tz).add(days=7).date()

    # Ищем даты в формате DD.MM-DD.MM
    date_part = None
    for part in parts:
        if "-" in part and "." in part:
            try:
                dates = part.split("-")
                now = pendulum.now(tz)
                sd = pendulum.parse(
                    f"{now.year}-{dates[0].split('.')[1]}-{dates[0].split('.')[0]}", tz=tz
                ).date()
                ed = pendulum.parse(
                    f"{now.year}-{dates[1].split('.')[1]}-{dates[1].split('.')[0]}", tz=tz
                ).date()
                start_date = sd
                end_date = ed
                date_part = part
                break
            except Exception:
                pass

    # Извлекаем название и город из текста без дат
    if date_part:
        remaining = text.replace(date_part, "").strip()
    else:
        remaining = text

    # Если остались слова после дат — первое слово/фраза = город, остальное = название
    if remaining:
        words = remaining.split()
        if len(words) >= 2:
            # Последнее слово может быть городом
            destination = words[-1]
            title = " ".join(words[:-1])
        else:
            title = remaining

    async with async_session() as session:
        # Проверяем, нет ли уже активной
        existing = await get_active_trip(session, message.from_user.id)
        if existing:
            await message.answer(
                f"У тебя уже есть активная командировка: {existing.title}\n"
                "Сначала заверши её: /trip off"
            )
            return

        trip = await create_trip(
            session,
            user_id=message.from_user.id,
            title=title,
            start_date=start_date,
            end_date=end_date,
            destination=destination,
        )

    await message.answer(
        f"✈️ Командировка создана: <b>{trip.title}</b>\n"
        f"📅 {trip.start_date.strftime('%d.%m')} — {trip.end_date.strftime('%d.%m')}",
        parse_mode="HTML",
    )


async def _trip_off(message: Message) -> None:
    """Завершить активную командировку."""
    async with async_session() as session:
        trip = await get_active_trip(session, message.from_user.id)
        if not trip:
            await message.answer("Нет активной командировки.")
            return

        await complete_trip(session, trip.id, message.from_user.id)

    await message.answer(
        f"✈️ Командировка «{trip.title}» завершена!\n"
        "Добро пожаловать обратно!"
    )
