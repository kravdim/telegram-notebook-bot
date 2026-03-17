"""Команды администратора: /prompts, /status, /adduser и др."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.llm.prompts import get_all_prompts

logger = logging.getLogger(__name__)

router = Router()


def _is_admin(user_id: int) -> bool:
    """Проверка прав администратора."""
    return user_id in settings.admin_telegram_ids


@router.message(Command("prompts"))
async def cmd_prompts(message: Message) -> None:
    """Показать список активных промптов (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    async with async_session() as session:
        prompts = await get_all_prompts(session)

    if not prompts:
        await message.answer("Нет активных промптов в БД.")
        return

    lines = ["<b>Активные промпты:</b>\n"]
    for p in prompts:
        preview = p.content[:80].replace("\n", " ")
        lines.append(f"• <b>{p.prompt_key}</b> v{p.version}\n  {preview}...")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Показать статус бота (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    from bot.handlers.messages import llm_client
    main_status = "healthy" if (llm_client and llm_client._main_healthy) else "down"

    await message.answer(
        f"<b>Статус бота</b>\n"
        f"Main LLM: {main_status}\n"
        f"Whitelist: {len(settings.allowed_telegram_ids)} users",
        parse_mode="HTML",
    )
