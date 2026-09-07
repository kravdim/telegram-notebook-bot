"""Явные команды создания и отмены диалога без облачного AI."""

from typing import cast

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.application.interactions import WorkflowType
from bot.services.interactions import interaction_service
from bot.services.local_commands import execute_local_command

router = Router()


@router.message(Command("add", "note", "remind"))
async def create_locally(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    value = (command.args or "").strip()
    if not value:
        await message.answer(
            "Без AI:\n/add Название задачи\n/note Текст заметки\n"
            "/remind 2030-01-01 09:00 Текст напоминания", parse_mode=None,
        )
        return
    result = await execute_local_command(message.from_user.id, message.chat.id, message.message_id,
                                         command.command, value)
    for offset in range(0, len(result.text), 1900):
        await message.answer(result.text[offset:offset + 1900], parse_mode=None)


@router.message(Command("cancel"))
async def cancel_dialog(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    current = await interaction_service.get(message.from_user.id)
    if current and current.state_type == "voice_processing":
        await message.answer("Подтверждённая команда уже выполняется. Проверь результат через /tasks.")
        return
    if current:
        await interaction_service.clear(message.from_user.id, cast(WorkflowType, current.state_type),
                                        current.payload.get("session_token"))
    await state.clear()
    await message.answer("Диалог отменён. Сохранённые задачи и записи остаются. Команды: /help; настройка: /start.")
