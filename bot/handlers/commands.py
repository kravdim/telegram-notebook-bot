"""Команды бота: /help, /tasks, /today, /frog, /done, /notes, /projects, /memoir, /chrono, /focus, /stats."""

import logging
import uuid as uuid_mod
from datetime import date, datetime, timedelta

import pendulum
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.chronometry import get_day_stats, get_week_stats
from bot.db.crud.memoir import get_memoir_entries, get_value_stats
from bot.db.crud.projects import get_project_progress, get_project_tasks, get_user_projects
from bot.db.crud.tasks import (
    complete_task_by_id,
    get_frog,
    get_today_tasks,
    get_user_tasks,
    search_tasks,
    set_frog,
)
from bot.db.crud.users import get_user, update_user_settings
from bot.db.engine import async_session
from bot.db.models import Note
from bot.formatters.chronometry import format_day_photo, format_week_summary
from bot.formatters.memoir import format_memoir_entries, format_value_stats

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
        "• Создай слона «ремонт кухни»\n\n"
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


# --- /notes ---

@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    """Список последних заметок."""
    if not message.from_user:
        return

    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Note)
            .where(Note.user_id == message.from_user.id)
            .order_by(Note.created_at.desc())
            .limit(10)
        )
        notes = list(result.scalars().all())

    if not notes:
        await message.answer("📝 Заметок пока нет. Напиши мне что-нибудь для сохранения!")
        return

    lines = ["📝 <b>Последние заметки:</b>\n"]
    for note in notes:
        title = note.title or note.content[:50]
        date_str = note.created_at.strftime("%d.%m") if note.created_at else ""
        tags = " ".join(f"#{t}" for t in note.tags) if note.tags else ""
        lines.append(f"• {title} <i>{date_str}</i> {tags}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# --- /projects ---

@router.message(Command("projects"))
async def cmd_projects(message: Message) -> None:
    """Список проектов (слонов)."""
    if not message.from_user:
        return

    async with async_session() as session:
        projects = await get_user_projects(session, message.from_user.id)

    if not projects:
        await message.answer(
            "🐘 Слонов пока нет.\n"
            "Напиши «создай слона Название» чтобы начать."
        )
        return

    lines = ["🐘 <b>Проекты (слоны):</b>\n"]
    for p in projects:
        async with async_session() as session:
            progress = await get_project_progress(session, p.id)
            tasks = await get_project_tasks(session, p.id)

        pct = progress["percent"]
        done = progress["done"]
        total = progress["total"]
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"• <b>{p.title}</b> [{bar}] {pct}% ({done}/{total})")

    await message.answer("\n".join(lines), parse_mode="HTML")


# --- /memoir ---

@router.message(Command("memoir"))
async def cmd_memoir(message: Message) -> None:
    """Мемуарник: последние записи + статистика ценностей."""
    if not message.from_user:
        return

    async with async_session() as session:
        entries = await get_memoir_entries(session, message.from_user.id, limit=7)
        stats = await get_value_stats(session, message.from_user.id)

    text = format_memoir_entries(entries)
    if stats:
        text += "\n\n" + format_value_stats(stats)

    await message.answer(text, parse_mode="HTML")


# --- /chrono ---

@router.message(Command("chrono"))
async def cmd_chrono(message: Message, command: CommandObject) -> None:
    """Хронометраж: дневная/недельная фотография."""
    if not message.from_user:
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    tz = user.timezone if user else "Europe/Moscow"

    args = command.args.strip().lower() if command.args else ""

    if args == "week":
        async with async_session() as session:
            stats = await get_week_stats(session, message.from_user.id, tz)
        await message.answer(format_week_summary(stats), parse_mode="HTML")
    else:
        async with async_session() as session:
            stats = await get_day_stats(session, message.from_user.id, tz)
        await message.answer(format_day_photo(stats), parse_mode="HTML")


# --- /focus ---

@router.message(Command("focus"))
async def cmd_focus(message: Message, command: CommandObject) -> None:
    """Режим фокуса: /focus 30 (минуты) или /focus off."""
    if not message.from_user:
        return

    args = command.args.strip().lower() if command.args else ""

    if args == "off":
        async with async_session() as session:
            await update_user_settings(
                session, message.from_user.id, focus_until=None
            )
        await message.answer("🎯 Режим фокуса выключен.")
        return

    minutes = 30
    if args.isdigit():
        minutes = int(args)
        if minutes > 480:
            minutes = 480

    focus_until = pendulum.now("UTC").add(minutes=minutes)

    async with async_session() as session:
        await update_user_settings(
            session, message.from_user.id, focus_until=focus_until
        )

    await message.answer(
        f"🎯 Режим фокуса на {minutes} мин.\n"
        "Не буду беспокоить хронометражем до окончания."
    )


# --- /stats ---

@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject) -> None:
    """Статистика: /stats frogs, /stats productivity, /stats values."""
    if not message.from_user:
        return

    args = command.args.strip().lower() if command.args else ""

    if args == "frogs":
        await _stats_frogs(message)
    elif args == "productivity":
        await _stats_productivity(message)
    elif args == "values":
        await _stats_values(message)
    else:
        await message.answer(
            "📊 <b>Статистика</b>\n\n"
            "/stats frogs — лягушки\n"
            "/stats productivity — продуктивность\n"
            "/stats values — ценности (мемуарник)",
            parse_mode="HTML",
        )


async def _stats_frogs(message: Message) -> None:
    """Статистика лягушек."""
    from sqlalchemy import select, func
    from bot.db.models import Task

    async with async_session() as session:
        # За последние 30 дней
        since = pendulum.now().subtract(days=30).date()

        result = await session.execute(
            select(func.count()).where(
                Task.user_id == message.from_user.id,
                Task.is_frog == True,
                Task.created_at >= pendulum.instance(
                    datetime.combine(since, datetime.min.time())
                ),
            )
        )
        total = result.scalar() or 0

        result = await session.execute(
            select(func.count()).where(
                Task.user_id == message.from_user.id,
                Task.is_frog == True,
                Task.status == "done",
                Task.created_at >= pendulum.instance(
                    datetime.combine(since, datetime.min.time())
                ),
            )
        )
        done = result.scalar() or 0

    from bot.formatters.stats import format_frog_stats
    text = format_frog_stats(done, total, 0)
    await message.answer(text, parse_mode="HTML")


async def _stats_productivity(message: Message) -> None:
    """Статистика продуктивности."""
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    tz = user.timezone if user else "Europe/Moscow"

    async with async_session() as session:
        week = await get_week_stats(session, message.from_user.id, tz)

    from bot.formatters.stats import format_productivity_stats
    text = format_productivity_stats(
        avg_week=week["avg_productivity"],
        avg_month=week["avg_productivity"],  # Упрощение
        trend="stable",
    )
    await message.answer(text, parse_mode="HTML")


async def _stats_values(message: Message) -> None:
    """Статистика ценностей."""
    async with async_session() as session:
        stats = await get_value_stats(session, message.from_user.id)

    text = format_value_stats(stats)
    await message.answer(text, parse_mode="HTML")


# --- /settings ---

@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Показать текущие настройки."""
    if not message.from_user:
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)

    if not user:
        await message.answer("Пользователь не найден. Используй /start для регистрации.")
        return

    work_days = ", ".join(
        {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}.get(d, "?")
        for d in (user.work_days or [])
    )

    await message.answer(
        f"⚙️ <b>Настройки</b>\n\n"
        f"Имя: {user.username}\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Утренний дайджест: {user.digest_morning_time.strftime('%H:%M')}\n"
        f"Вечерний итог: {user.digest_evening_time.strftime('%H:%M')}\n"
        f"Мемуарник: {user.memoir_prompt_time.strftime('%H:%M')}\n"
        f"Рабочие дни: {work_days}\n"
        f"Рабочее время: {user.work_start_time.strftime('%H:%M')} — {user.work_end_time.strftime('%H:%M')}\n"
        f"Хронометраж: {'✅' if user.chronometry_enabled else '❌'}"
        f" (каждые {user.chronometry_interval_min} мин)\n"
        f"Дайджесты: {'✅' if user.digest_enabled else '❌'}",
        parse_mode="HTML",
    )
