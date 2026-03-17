"""Команды бота: /help, /tasks, /today, /frog, /done."""

import logging
import uuid as uuid_mod
from datetime import date

import pendulum
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.tasks import (
    complete_task_by_id,
    get_frog,
    get_today_tasks,
    get_user_tasks,
    search_tasks,
    set_frog,
)
from bot.db.crud.users import get_user
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

router = Router()

_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "normal": "⚪"}


def _format_task(task) -> str:
    """Форматирование задачи для вывода."""
    emoji = _PRIORITY_EMOJI.get(task.priority, "⚪")
    frog = "🐸 " if task.is_frog else ""
    due = ""
    if task.due_date:
        due = f" 📅 {task.due_date.strftime('%d.%m')}"
    if task.due_time:
        due += f" ⏰ {task.due_time.strftime('%H:%M')}"
    return f"{frog}{emoji} {task.title}{due}"


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка по боту."""
    await message.answer(
        "📋 <b>Доступные команды:</b>\n\n"
        "/start — начать работу / настройка\n"
        "/help — эта справка\n"
        "/today — задачи на сегодня\n"
        "/tasks — все открытые задачи\n"
        "/frog — лягушка дня\n"
        "/done — отметить задачу выполненной\n"
        "/notes — заметки\n"
        "/projects — проекты (слоны)\n"
        "/memoir — мемуарник\n"
        "/chrono — хронометраж\n"
        "/focus — режим фокуса\n"
        "/trip — режим командировки\n"
        "/stats — статистика\n"
        "/settings — настройки\n\n"
        "💡 <b>Примеры фраз:</b>\n"
        "• Купить продукты завтра\n"
        "• Напомни в 15:00 позвонить врачу\n"
        "• Что у меня на сегодня?\n"
        "• Отметь задачу «купить продукты» выполненной\n"
        "• Создай проект «ремонт кухни»\n\n"
        "Также можно отправлять голосовые сообщения 🎤",
        parse_mode="HTML",
    )


# --- /tasks ---

@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    """Список открытых задач."""
    if not message.from_user:
        return

    async with async_session() as session:
        tasks = await get_user_tasks(session, message.from_user.id, status="open")

    if not tasks:
        await message.answer("У тебя нет открытых задач. Свободен! 🎉")
        return

    lines = ["<b>📌 Открытые задачи:</b>\n"]
    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. {_format_task(task)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# --- /today ---

@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    """Задачи на сегодня."""
    if not message.from_user:
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
        tz = user.timezone if user else "Europe/Moscow"
        today = pendulum.now(tz).date()
        tasks = await get_today_tasks(session, message.from_user.id, today)

    if not tasks:
        await message.answer("На сегодня задач нет. Хорошего дня! ☀️")
        return

    lines = ["<b>📅 Задачи на сегодня:</b>\n"]
    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. {_format_task(task)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# --- /frog ---

@router.message(Command("frog"))
async def cmd_frog(message: Message) -> None:
    """Показать/назначить лягушку дня."""
    if not message.from_user:
        return

    async with async_session() as session:
        frog = await get_frog(session, message.from_user.id)

    if frog:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Съедена!", callback_data=f"frog_done:{frog.id}")
        await message.answer(
            f"🐸 <b>Лягушка дня:</b>\n{frog.title}",
            parse_mode="HTML",
            reply_markup=kb.as_markup(),
        )
        return

    # Нет лягушки — предложить выбрать из открытых задач
    async with async_session() as session:
        tasks = await get_user_tasks(session, message.from_user.id, status="open")

    if not tasks:
        await message.answer("Нет открытых задач для назначения лягушки.")
        return

    kb = InlineKeyboardBuilder()
    for task in tasks[:8]:  # Макс 8 кнопок
        kb.button(
            text=f"{task.title[:40]}",
            callback_data=f"set_frog:{task.id}",
        )
    kb.adjust(1)

    await message.answer(
        "🐸 Лягушка не назначена. Какое дело сегодня самое неприятное?",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("set_frog:"))
async def cb_set_frog(callback: CallbackQuery) -> None:
    """Назначить лягушку."""
    task_id = uuid_mod.UUID(callback.data.split(":", 1)[1])
    await callback.answer()

    async with async_session() as session:
        task = await set_frog(session, task_id, callback.from_user.id)

    if task:
        await callback.message.edit_text(
            f"🐸 Лягушка дня назначена: <b>{task.title}</b>\n"
            "Съешь её первой — остаток дня будет легче!",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text("Не удалось назначить лягушку.")


@router.callback_query(F.data.startswith("frog_done:"))
async def cb_frog_done(callback: CallbackQuery) -> None:
    """Отметить лягушку выполненной."""
    task_id = uuid_mod.UUID(callback.data.split(":", 1)[1])
    await callback.answer("🎉 Молодец!")

    async with async_session() as session:
        task = await complete_task_by_id(session, task_id, callback.from_user.id)

    if task:
        await callback.message.edit_text(
            f"🐸✅ Лягушка «{task.title}» съедена! Отличная работа! 🎉",
            parse_mode="HTML",
        )


# --- /done ---

@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject) -> None:
    """Отметить задачу выполненной: /done текст поиска."""
    if not message.from_user:
        return

    query = command.args.strip() if command.args else ""

    if not query:
        # Показать список для выбора
        async with async_session() as session:
            tasks = await get_user_tasks(session, message.from_user.id, status="open")

        if not tasks:
            await message.answer("Нет открытых задач.")
            return

        kb = InlineKeyboardBuilder()
        for task in tasks[:10]:
            kb.button(
                text=f"✅ {task.title[:40]}",
                callback_data=f"task_done:{task.id}",
            )
        kb.adjust(1)

        await message.answer(
            "Какую задачу отметить выполненной?",
            reply_markup=kb.as_markup(),
        )
        return

    # Fuzzy-поиск
    async with async_session() as session:
        tasks = await search_tasks(session, message.from_user.id, query)

    if not tasks:
        await message.answer(f"Не нашёл задачу по запросу «{query}».")
        return

    if len(tasks) == 1:
        async with async_session() as session:
            task = await complete_task_by_id(session, tasks[0].id, message.from_user.id)
        if task:
            await message.answer(f"✅ Задача «{task.title}» выполнена! 🎉")
        return

    # Несколько совпадений — предложить выбрать
    kb = InlineKeyboardBuilder()
    for task in tasks[:5]:
        kb.button(
            text=f"✅ {task.title[:40]}",
            callback_data=f"task_done:{task.id}",
        )
    kb.adjust(1)

    await message.answer(
        "Нашёл несколько задач. Какую отметить?",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("task_done:"))
async def cb_task_done(callback: CallbackQuery) -> None:
    """Отметить задачу выполненной через inline-кнопку."""
    task_id = uuid_mod.UUID(callback.data.split(":", 1)[1])
    await callback.answer("✅")

    async with async_session() as session:
        task = await complete_task_by_id(session, task_id, callback.from_user.id)

    if task:
        await callback.message.edit_text(
            f"✅ Задача «{task.title}» выполнена! 🎉",
            parse_mode="HTML",
        )
