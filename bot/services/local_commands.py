"""Локальный CRUD и журнал результата в одной короткой транзакции."""

import hashlib
from dataclasses import asdict

import pendulum
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from bot.application.command_bus import CommandResult
from bot.db.crud.notes import create_note
from bot.db.crud.reminders import create_reminder
from bot.db.crud.tasks import create_task
from bot.db.crud.users import get_user
from bot.db.engine import async_session
from bot.db.models import ProcessedRequest


async def _apply(session, user, command: str, value: str) -> CommandResult:
    if command == "note":
        await create_note(session, user.telegram_id, value, commit=False)
        return CommandResult("📝 Заметка сохранена.")
    if command == "add":
        if len(value) > 500:
            return CommandResult("Сократи название задачи до 500 символов.", "error")
        await create_task(session, user.telegram_id, value, commit=False)
        return CommandResult(f"✅ Задача создана: {value}")
    if command != "remind":
        raise ValueError("Unsupported local command")
    parts = value.split(maxsplit=2)
    try:
        when = pendulum.from_format(f"{parts[0]} {parts[1]}", "YYYY-MM-DD HH:mm", tz=user.timezone)
        if len(parts) != 3 or when <= pendulum.now(user.timezone):
            raise ValueError("Future time and text required")
    except (ValueError, IndexError):
        return CommandResult("Укажи будущую дату, время и текст: /remind 2030-01-01 09:00 Позвонить", "error")
    await create_reminder(session, user.telegram_id, parts[2], when, timezone=user.timezone, commit=False)
    return CommandResult(f"🔔 Напомню {when.format('DD.MM.YYYY HH:mm')} ({user.timezone}): {parts[2]}")


async def execute_local_command(user_id: int, chat_id: int, message_id: int, command: str, value: str) -> CommandResult:
    if not value.strip():
        return CommandResult("Добавь текст после команды.", "error")
    key = hashlib.sha256(f"local:{user_id}:{chat_id}:{message_id}".encode()).hexdigest()
    async with async_session() as session:
        user = await get_user(session, user_id)
        if user is None:
            return CommandResult("Сначала выполни /start.", "error")
        await session.execute(insert(ProcessedRequest).values(request_key=key, user_id=user_id)
                              .on_conflict_do_nothing(index_elements=[ProcessedRequest.request_key]))
        row = await session.scalar(select(ProcessedRequest).where(
            ProcessedRequest.request_key == key, ProcessedRequest.user_id == user_id,
        ).with_for_update())
        if row is None:
            raise RuntimeError("Local request ownership mismatch")
        if "0" in row.action_results:
            return CommandResult(**row.action_results["0"])
        result = await _apply(session, user, command, value.strip())
        if result.kind == "error":
            await session.rollback()
            return result
        row.action_results = {"0": asdict(result)}
        row.status = "completed"
        row.completed_at = pendulum.now("UTC")
        await session.commit()
        return result
