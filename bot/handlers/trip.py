"""Обработчик командировок: /trip on, /trip off."""

import logging
import re
from html import escape

import pendulum
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db.crud.trips import complete_trip, create_trip, get_active_trip, get_open_trip
from bot.db.crud.tasks import get_user_tasks
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
            user = await get_user(session, message.from_user.id)
            tz = user.timezone if user else "Europe/Moscow"
            trip = await get_open_trip(
                session, message.from_user.id, pendulum.now(tz).date()
            )
            trip_tasks = (
                [task for task in await get_user_tasks(session, message.from_user.id)
                 if task.trip_id == trip.id]
                if trip else []
            )

        if trip:
            task_lines = ""
            if trip_tasks:
                task_lines = "\n\n📋 <b>Задачи поездки:</b>\n" + "\n".join(
                    f"• {escape(task.title)}" for task in trip_tasks[:10]
                )
            state = "Запланирована" if trip.start_date > pendulum.now(tz).date() else "Командировка"
            await message.answer(
                f"✈️ <b>{state}:</b> {escape(trip.title)}\n"
                f"📍 {escape(trip.destination) if trip.destination else '—'}\n"
                f"📅 {trip.start_date.strftime('%d.%m')} — {trip.end_date.strftime('%d.%m')}"
                f"{task_lines}",
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

    title = text
    destination = None
    start_date = pendulum.now(tz).date()
    end_date = pendulum.now(tz).add(days=7).date()

    date_match = re.search(
        r"(?P<sd>\d{1,2})\.(?P<sm>\d{1,2})(?:\.(?P<sy>\d{4}))?"
        r"\s*[-–—]\s*"
        r"(?P<ed>\d{1,2})\.(?P<em>\d{1,2})(?:\.(?P<ey>\d{4}))?",
        text,
    )
    date_part = date_match.group(0) if date_match else None
    if date_match:
        try:
            current_year = pendulum.now(tz).year
            start_year = int(date_match.group("sy") or current_year)
            start_date = pendulum.date(
                start_year,
                int(date_match.group("sm")),
                int(date_match.group("sd")),
            )
            end_year = int(date_match.group("ey") or start_year)
            end_date = pendulum.date(
                end_year,
                int(date_match.group("em")),
                int(date_match.group("ed")),
            )
            if not date_match.group("ey") and end_date < start_date:
                end_date = end_date.add(years=1)
            if end_date < start_date:
                raise ValueError("end before start")
        except (ValueError, OverflowError):
            await message.answer("Не удалось распознать даты командировки. Проверь диапазон ДД.ММ-ДД.ММ.")
            return

    # Извлекаем название и город из текста без дат
    if date_part:
        remaining = text.replace(date_part, "").strip()
    else:
        remaining = text

    # Сохраняем полное осмысленное название; город извлекаем только из явной
    # конструкции «в <город>», не угадывая по последнему слову.
    if remaining:
        title = remaining
        destination_match = re.search(r"\bв\s+([А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)*)", remaining)
        if destination_match:
            destination = destination_match.group(1)
        elif len(remaining.split()) == 1:
            destination = remaining
            title = f"Командировка: {remaining}"

    async with async_session() as session:
        # Проверяем, нет ли уже активной
        existing = await get_open_trip(
            session, message.from_user.id, pendulum.now(tz).date()
        )
        if existing:
            await message.answer(
                f"У тебя уже есть незавершённая командировка: {escape(existing.title)}\n"
                "Сначала заверши её: /trip off",
                parse_mode="HTML",
            )
            return

        trip = await create_trip(
            session,
            user_id=message.from_user.id,
            title=title,
            start_date=start_date,
            end_date=end_date,
            destination=destination,
            timezone=tz,
        )

    await message.answer(
        f"✈️ Командировка создана: <b>{escape(trip.title)}</b>\n"
        f"📅 {trip.start_date.strftime('%d.%m')} — {trip.end_date.strftime('%d.%m')}",
        parse_mode="HTML",
    )


async def _trip_off(message: Message) -> None:
    """Завершить активную командировку."""
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
        tz = user.timezone if user else "Europe/Moscow"
        trip = await get_open_trip(
            session, message.from_user.id, pendulum.now(tz).date()
        )
        if not trip:
            await message.answer("Нет активной командировки.")
            return

        await complete_trip(session, trip.id, message.from_user.id)

    await message.answer(
        f"✈️ Командировка «{escape(trip.title)}» завершена!\n"
        "Добро пожаловать обратно!",
        parse_mode="HTML",
    )
