"""Команды администратора: /prompts, /status, /adduser, /removeuser."""

import logging
from html import escape as html_escape
from pathlib import Path

import anyio
import yaml
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings, BASE_DIR
from bot.db.crud.users import get_all_users
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
        preview = html_escape(p.content[:80].replace("\n", " "))
        lines.append(f"• <b>{html_escape(p.prompt_key)}</b> v{p.version}\n  {preview}...")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Показать расширенный статус бота (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    from bot.handlers.messages import llm_client
    from bot.scheduler.healthcheck import check_all_health

    if not llm_client:
        await message.answer("LLM-клиент не инициализирован.")
        return

    health = await check_all_health(llm_client)

    async with async_session() as session:
        users = await get_all_users(session)

    lines = ["<b>Статус бота</b>\n"]

    for service, info in health.items():
        status = info.get("status", "?")
        emoji = {"ok": "🟢", "degraded": "🟡", "error": "🔴", "not_configured": "⚪"}.get(
            status, "⚪"
        )
        latency = f" ({info['latency_ms']}ms)" if "latency_ms" in info else ""
        lines.append(f"{emoji} {service}: {status}{latency}")

    lines.append(f"\n👥 Пользователей: {len(users)}")
    lines.append(f"📋 Whitelist: {len(settings.allowed_telegram_ids)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject) -> None:
    """Добавить пользователя в whitelist (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    if not command.args:
        await message.answer("Использование: /adduser TELEGRAM_ID")
        return

    try:
        user_id = int(command.args.strip())
    except ValueError:
        await message.answer("Telegram ID должен быть числом.")
        return

    if user_id not in settings.allowed_telegram_ids:
        settings.allowed_telegram_ids.append(user_id)
        await _persist_whitelist()

    await message.answer(f"✅ Пользователь {user_id} добавлен в whitelist.")


@router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject) -> None:
    """Удалить пользователя из whitelist (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    if not command.args:
        await message.answer("Использование: /removeuser TELEGRAM_ID")
        return

    try:
        user_id = int(command.args.strip())
    except ValueError:
        await message.answer("Telegram ID должен быть числом.")
        return

    if user_id in settings.allowed_telegram_ids:
        settings.allowed_telegram_ids.remove(user_id)
        await _persist_whitelist()

    await message.answer(f"✅ Пользователь {user_id} удалён из whitelist.")


@router.message(Command("listusers"))
async def cmd_listusers(message: Message) -> None:
    """Показать всех пользователей (admin)."""
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return

    async with async_session() as session:
        users = await get_all_users(session)

    if not users:
        await message.answer("Пользователей нет.")
        return

    lines = ["<b>Пользователи:</b>\n"]
    for u in users:
        role = "👑" if u.role == "admin" else "👤"
        lines.append(f"{role} {u.username} ({u.telegram_id})")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _persist_whitelist() -> None:
    """Сохранить текущий whitelist в config.yaml."""
    config_path = BASE_DIR / "config.yaml"
    try:
        async with await anyio.open_file(config_path, "r", encoding="utf-8") as f:
            content = await f.read()
        cfg = yaml.safe_load(content) or {}
        cfg.setdefault("bot", {})["allowed_telegram_ids"] = list(settings.allowed_telegram_ids)
        async with await anyio.open_file(config_path, "w", encoding="utf-8") as f:
            await f.write(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
        logger.info("Whitelist сохранён в config.yaml: %s", settings.allowed_telegram_ids)
    except Exception as e:
        logger.error("Не удалось сохранить whitelist в config.yaml: %s", e)
