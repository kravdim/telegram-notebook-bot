"""Команды бота: /help, /tasks, /today, /frog, /done, /notes, /projects, /memoir, /chrono, /focus, /stats."""

import html
import logging
import uuid as uuid_mod
from datetime import date, datetime, timedelta

import pendulum
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.birthdays import get_all_birthdays, get_upcoming_birthdays
from bot.db.crud.chronometry import get_day_stats, get_week_stats
from bot.db.crud.memoir import get_memoir_entries, get_value_stats
from bot.db.crud.projects import get_project_progress, get_project_tasks, get_user_projects
from bot.db.crud.tasks import (
    get_frog,
    get_today_tasks,
    get_user_tasks,
    search_tasks,
    set_frog,
    task_title_similarity,
)
from bot.db.crud.users import get_user, update_user_settings
from bot.db.engine import async_session
from bot.db.models import Note
from bot.formatters import split_html_message
from bot.formatters.chronometry import format_day_photo, format_week_summary
from bot.formatters.memoir import format_memoir_entries, format_value_stats
from bot.services.tasks import complete_task_workflow

logger = logging.getLogger(__name__)

router = Router()

_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "normal": "⚪"}


async def _answer_html_parts(message: Message, text: str) -> None:
    """Отправить потенциально длинный HTML без превышения лимита Telegram."""
    for part in split_html_message(text):
        await message.answer(part, parse_mode="HTML")


def _birthday_in_year(birth_date: date, year: int) -> date:
    """Дата празднования в году; 29 февраля — 28 февраля в невисокосный."""
    try:
        return birth_date.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def _format_task(task) -> str:
    """Форматирование задачи для вывода."""
    emoji = _PRIORITY_EMOJI.get(task.priority, "⚪")
    frog = "🐸 " if task.is_frog else ""
    due = ""
    if task.due_date:
        due = f" 📅 {task.due_date.strftime('%d.%m')}"
    if task.due_time:
        due += f" ⏰ {task.due_time.strftime('%H:%M')}"
    return f"{frog}{emoji} {html.escape(task.title)}{due}"


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
        "/chrono — фото дня; /chrono week — неделя\n"
        "/focus — статус; /focus 30 — включить\n"
        "/trip — режим командировки\n"
        "/birthdays — дни рождения\n"
        "/stats — статистика\n"
        "/settings — настройки\n"
        "/export — выгрузить данные\n\n"
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

    await _answer_html_parts(message, "\n".join(lines))


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

    await _answer_html_parts(message, "\n".join(lines))


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
            f"🐸 <b>Лягушка дня:</b>\n{html.escape(frog.title)}",
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
            f"🐸 Лягушка дня назначена: <b>{html.escape(task.title)}</b>\n"
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
        user = await get_user(session, callback.from_user.id)
        completion = await complete_task_workflow(
            session,
            task_id,
            callback.from_user.id,
            user.timezone if user else "Europe/Moscow",
        )
        task = completion.task

    if task:
        next_text = (
            f"\n🔄 Следующая: {completion.next_date.strftime('%d.%m')}"
            if completion.next_date else ""
        )
        await callback.message.edit_text(
            f"🐸✅ Лягушка «{html.escape(task.title)}» съедена! Отличная работа! 🎉"
            f"{next_text}",
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
        tasks = await search_tasks(session, message.from_user.id, query, status="open")

    if not tasks:
        await message.answer(f"Не нашёл задачу по запросу «{query}».")
        return

    exact = next(
        (task for task in tasks if task_title_similarity(query, task.title) == 1.0),
        None,
    )
    confident = exact or (
        tasks[0]
        if len(tasks) == 1 and task_title_similarity(query, tasks[0].title) >= 0.6
        else None
    )
    if confident:
        async with async_session() as session:
            user = await get_user(session, message.from_user.id)
            completion = await complete_task_workflow(
                session,
                confident.id,
                message.from_user.id,
                user.timezone if user else "Europe/Moscow",
            )
        if completion.task:
            next_text = (
                f"\n🔄 Следующая: {completion.next_date.strftime('%d.%m')}"
                if completion.next_date else ""
            )
            await message.answer(
                f"✅ Задача «{completion.task.title}» выполнена! 🎉{next_text}"
            )
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
        user = await get_user(session, callback.from_user.id)
        completion = await complete_task_workflow(
            session,
            task_id,
            callback.from_user.id,
            user.timezone if user else "Europe/Moscow",
        )
        task = completion.task

    if task:
        next_text = (
            f"\n🔄 Следующая: {completion.next_date.strftime('%d.%m')}"
            if completion.next_date else ""
        )
        await callback.message.edit_text(
            f"✅ Задача «{html.escape(task.title)}» выполнена! 🎉{next_text}",
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
        lines.append(_format_note_line(note))

    await _answer_html_parts(message, "\n".join(lines))


def _format_note_line(note: Note) -> str:
    """Показать не только заголовок, но и полезное содержимое заметки."""
    raw_title = note.title or note.content[:50]
    title = html.escape(raw_title)
    content = note.content.strip()
    preview = ""
    if note.title and content and content != note.title:
        shortened = content if len(content) <= 160 else content[:157] + "…"
        preview = f" — {html.escape(shortened)}"
    date_str = note.created_at.strftime("%d.%m") if note.created_at else ""
    tags = " ".join(f"#{html.escape(t)}" for t in note.tags) if note.tags else ""
    return f"• {title}{preview} <i>{date_str}</i> {tags}"


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
    async with async_session() as session:
        from bot.db.crud.projects import batch_project_progress
        progress_map = await batch_project_progress(session, [p.id for p in projects])
        for p in projects:
            progress = progress_map[p.id]
            pct = progress["percent"]
            done = progress["done"]
            total = progress["total"]
            bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"• <b>{html.escape(p.title)}</b> [{bar}] {pct}% ({done}/{total})")

    await _answer_html_parts(message, "\n".join(lines))


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

    await _answer_html_parts(message, text)


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
    elif not args:
        async with async_session() as session:
            stats = await get_day_stats(session, message.from_user.id, tz)
        await message.answer(format_day_photo(stats), parse_mode="HTML")
    else:
        await message.answer(
            "Не понял аргумент. Используй /chrono или /chrono week."
        )


# --- /focus ---

@router.message(Command("focus"))
async def cmd_focus(message: Message, command: CommandObject) -> None:
    """Режим фокуса: /focus 30 (минуты) или /focus off."""
    if not message.from_user:
        return

    args = command.args.strip().lower() if command.args else ""

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    tz = user.timezone if user else "Europe/Moscow"

    if not args:
        focus_until = user.focus_until if user else None
        if focus_until and pendulum.instance(focus_until) > pendulum.now("UTC"):
            local_until = pendulum.instance(focus_until).in_timezone(tz)
            await message.answer(
                f"🎯 Режим фокуса включён до {local_until.strftime('%H:%M')}.\n"
                "Выключить: /focus off"
            )
        else:
            await message.answer(
                "🎯 Режим фокуса выключен.\n"
                "Включить: /focus 30 (от 1 до 480 минут)."
            )
        return

    if args == "off":
        async with async_session() as session:
            await update_user_settings(
                session, message.from_user.id, focus_until=None
            )
        await message.answer("🎯 Режим фокуса выключен.")
        return

    if not args.isdigit():
        await message.answer("Укажи минуты числом, например: /focus 30")
        return

    minutes = int(args)
    if not 1 <= minutes <= 480:
        await message.answer("Длительность фокуса — от 1 до 480 минут.")
        return

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
    from sqlalchemy import func, or_, select
    from bot.db.models import Task

    async with async_session() as session:
        # За последние 30 дней
        since = pendulum.now().subtract(days=30).date()

        result = await session.execute(
            select(func.count()).where(
                Task.user_id == message.from_user.id,
                Task.is_frog == True,
                or_(
                    Task.created_at >= pendulum.instance(
                        datetime.combine(since, datetime.min.time())
                    ),
                    Task.completed_at >= pendulum.instance(
                        datetime.combine(since, datetime.min.time())
                    ),
                ),
            )
        )
        total = result.scalar() or 0

        result = await session.execute(
            select(func.count()).where(
                Task.user_id == message.from_user.id,
                Task.is_frog == True,
                Task.status == "done",
                Task.completed_at >= pendulum.instance(
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

    # Месячная статистика: берём 4 недели назад
    async with async_session() as session:
        month_stats = await get_week_stats(
            session, message.from_user.id, tz,
            days_back=28,
        )

    from bot.formatters.stats import format_productivity_stats
    trend = _productivity_trend(
        week["avg_productivity"], month_stats["avg_productivity"]
    )
    text = format_productivity_stats(
        avg_week=week["avg_productivity"],
        avg_month=month_stats["avg_productivity"],
        trend=trend,
    )
    await message.answer(text, parse_mode="HTML")


def _productivity_trend(avg_week: float, avg_month: float) -> str:
    delta = avg_week - avg_month
    if delta >= 0.2:
        return "up"
    if delta <= -0.2:
        return "down"
    return "stable"


async def _stats_values(message: Message) -> None:
    """Статистика ценностей."""
    async with async_session() as session:
        stats = await get_value_stats(session, message.from_user.id)

    text = format_value_stats(stats)
    await message.answer(text, parse_mode="HTML")


# --- /settings ---

_DAY_NAMES = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}


def _build_settings_kb(user) -> InlineKeyboardBuilder:
    """Собрать клавиатуру настроек."""
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"🌍 Часовой пояс: {user.timezone}",
        callback_data="settings:timezone",
    )
    kb.button(
        text=f"🌅 Утренний дайджест: {user.digest_morning_time.strftime('%H:%M')}",
        callback_data="settings:morning_time",
    )
    kb.button(
        text=f"🌇 Вечерний итог: {user.digest_evening_time.strftime('%H:%M')}",
        callback_data="settings:evening_time",
    )
    kb.button(
        text=f"📔 Мемуарник: {user.memoir_prompt_time.strftime('%H:%M')}",
        callback_data="settings:memoir_time",
    )
    kb.button(
        text=f"🏢 Начало дня: {user.work_start_time.strftime('%H:%M')}",
        callback_data="settings:work_start",
    )
    kb.button(
        text=f"🏠 Конец дня: {user.work_end_time.strftime('%H:%M')}",
        callback_data="settings:work_end",
    )
    days_str = ", ".join(_DAY_NAMES.get(d, "?") for d in (user.work_days or []))
    kb.button(text=f"📅 Рабочие дни: {days_str}", callback_data="settings:work_days")

    chrono_icon = "✅" if user.chronometry_enabled else "❌"
    kb.button(
        text=f"⏱ Хронометраж: {chrono_icon} ({user.chronometry_interval_min} мин)",
        callback_data="settings:chrono",
    )

    digest_icon = "✅" if user.digest_enabled else "❌"
    kb.button(text=f"📋 Дайджесты: {digest_icon}", callback_data="settings:digest_toggle")
    kb.adjust(1)
    return kb


def _format_settings_text(user) -> str:
    """Текст настроек."""
    days_str = ", ".join(_DAY_NAMES.get(d, "?") for d in (user.work_days or []))
    return (
        f"⚙️ <b>Настройки</b>\n\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Утренний дайджест: {user.digest_morning_time.strftime('%H:%M')}\n"
        f"Вечерний итог: {user.digest_evening_time.strftime('%H:%M')}\n"
        f"Мемуарник: {user.memoir_prompt_time.strftime('%H:%M')}\n"
        f"Рабочие дни: {days_str}\n"
        f"Рабочее время: {user.work_start_time.strftime('%H:%M')} — "
        f"{user.work_end_time.strftime('%H:%M')}\n"
        f"Хронометраж: {'✅' if user.chronometry_enabled else '❌'}"
        f" (каждые {user.chronometry_interval_min} мин)\n"
        f"Дайджесты: {'✅' if user.digest_enabled else '❌'}\n\n"
        f"Нажми кнопку чтобы изменить:"
    )


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

    await message.answer(
        _format_settings_text(user),
        parse_mode="HTML",
        reply_markup=_build_settings_kb(user).as_markup(),
    )


# --- Settings callbacks ---

async def _show_settings(callback: CallbackQuery) -> None:
    """Вернуться к главному экрану настроек."""
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
    if not user:
        await callback.message.edit_text("Пользователь не найден. Используй /start.")
        return
    await callback.message.edit_text(
        _format_settings_text(user),
        parse_mode="HTML",
        reply_markup=_build_settings_kb(user).as_markup(),
    )


def _time_picker_kb(callback_prefix: str, start: int = 6, end: int = 23) -> InlineKeyboardBuilder:
    """Клавиатура выбора времени (целые часы + :30)."""
    kb = InlineKeyboardBuilder()
    for hour in range(start, end + 1):
        kb.button(text=f"{hour:02d}:00", callback_data=f"{callback_prefix}:{hour:02d}:00")
        if hour < end:
            kb.button(text=f"{hour:02d}:30", callback_data=f"{callback_prefix}:{hour:02d}:30")
    kb.button(text="◀️ Назад", callback_data="settings:back")
    kb.adjust(4)
    return kb


@router.callback_query(F.data == "settings:back")
async def cb_settings_back(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_settings(callback)


@router.callback_query(F.data == "settings:timezone")
async def cb_timezone(callback: CallbackQuery) -> None:
    await callback.answer()
    choices = [
        ("Калининград", "Europe/Kaliningrad"),
        ("Москва", "Europe/Moscow"),
        ("Самара", "Europe/Samara"),
        ("Екатеринбург", "Asia/Yekaterinburg"),
        ("Омск", "Asia/Omsk"),
        ("Новосибирск", "Asia/Novosibirsk"),
        ("Иркутск", "Asia/Irkutsk"),
        ("Владивосток", "Asia/Vladivostok"),
    ]
    kb = InlineKeyboardBuilder()
    for label, timezone in choices:
        kb.button(text=label, callback_data=f"settings:set_tz:{timezone}")
    kb.button(text="◀️ Назад", callback_data="settings:back")
    kb.adjust(2)
    await callback.message.edit_text(
        "🌍 Выбери часовой пояс:", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("settings:set_tz:"))
async def cb_set_timezone(callback: CallbackQuery) -> None:
    await callback.answer()
    timezone = callback.data.split(":", 2)[2]
    try:
        pendulum.now(timezone)
    except Exception:
        await callback.message.edit_text("Неизвестный часовой пояс.")
        return
    async with async_session() as session:
        await update_user_settings(
            session, callback.from_user.id, timezone=timezone
        )
    await _show_settings(callback)


# --- Время утреннего дайджеста ---

@router.callback_query(F.data == "settings:morning_time")
async def cb_morning_time(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = _time_picker_kb("settings:set_morning", start=6, end=11)
    await callback.message.edit_text("🌅 Выбери время утреннего дайджеста:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("settings:set_morning:"))
async def cb_set_morning(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    from datetime import time as dt_time
    t = dt_time(int(parts[2]), int(parts[3]))
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, digest_morning_time=t)
    await _show_settings(callback)


# --- Время вечернего итога ---

@router.callback_query(F.data == "settings:evening_time")
async def cb_evening_time(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = _time_picker_kb("settings:set_evening", start=18, end=23)
    await callback.message.edit_text("🌇 Выбери время вечернего итога:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("settings:set_evening:"))
async def cb_set_evening(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    from datetime import time as dt_time
    t = dt_time(int(parts[2]), int(parts[3]))
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, digest_evening_time=t)
    await _show_settings(callback)


# --- Время мемуарника ---

@router.callback_query(F.data == "settings:memoir_time")
async def cb_memoir_time(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = _time_picker_kb("settings:set_memoir", start=18, end=23)
    await callback.message.edit_text("📔 Выбери время вопроса мемуарника:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("settings:set_memoir:"))
async def cb_set_memoir(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    from datetime import time as dt_time
    t = dt_time(int(parts[2]), int(parts[3]))
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, memoir_prompt_time=t)
    await _show_settings(callback)


# --- Начало рабочего дня ---

@router.callback_query(F.data == "settings:work_start")
async def cb_work_start(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = _time_picker_kb("settings:set_wstart", start=6, end=12)
    await callback.message.edit_text("🏢 Выбери начало рабочего дня:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("settings:set_wstart:"))
async def cb_set_work_start(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    from datetime import time as dt_time
    t = dt_time(int(parts[2]), int(parts[3]))
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, work_start_time=t)
    await _show_settings(callback)


# --- Конец рабочего дня ---

@router.callback_query(F.data == "settings:work_end")
async def cb_work_end(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = _time_picker_kb("settings:set_wend", start=15, end=23)
    await callback.message.edit_text("🏠 Выбери конец рабочего дня:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("settings:set_wend:"))
async def cb_set_work_end(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    from datetime import time as dt_time
    t = dt_time(int(parts[2]), int(parts[3]))
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, work_end_time=t)
    await _show_settings(callback)


# --- Рабочие дни ---

@router.callback_query(F.data == "settings:work_days")
async def cb_work_days(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
    current = set(user.work_days or [])

    kb = InlineKeyboardBuilder()
    for d in range(1, 8):
        check = "✅" if d in current else "⬜"
        kb.button(text=f"{check} {_DAY_NAMES[d]}", callback_data=f"settings:toggle_day:{d}")
    kb.button(text="◀️ Назад", callback_data="settings:back")
    kb.adjust(4)

    await callback.message.edit_text(
        "📅 Нажми на день чтобы вкл/выкл:", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("settings:toggle_day:"))
async def cb_toggle_day(callback: CallbackQuery) -> None:
    day = int(callback.data.split(":")[2])
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
        current = set(user.work_days or [])
        if day in current:
            if len(current) == 1:
                await callback.answer(
                    "Нужен хотя бы один рабочий день.", show_alert=True
                )
                return
            current.discard(day)
        else:
            current.add(day)
        await update_user_settings(session, callback.from_user.id, work_days=sorted(current))
    await callback.answer()
    # Перерисовать кнопки дней
    await cb_work_days(callback)


# --- Хронометраж (вкл/выкл + интервал) ---

@router.callback_query(F.data == "settings:chrono")
async def cb_chrono(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)

    kb = InlineKeyboardBuilder()
    chrono_label = "🔴 Выключить" if user.chronometry_enabled else "🟢 Включить"
    kb.button(text=chrono_label, callback_data="settings:chrono_toggle")
    for mins in (15, 30, 45, 60, 90, 120):
        mark = " ✓" if user.chronometry_interval_min == mins else ""
        kb.button(text=f"{mins} мин{mark}", callback_data=f"settings:chrono_set:{mins}")
    kb.button(text="◀️ Назад", callback_data="settings:back")
    kb.adjust(1, 3, 3, 1)

    await callback.message.edit_text(
        f"⏱ <b>Хронометраж</b>\n\n"
        f"Статус: {'✅ включён' if user.chronometry_enabled else '❌ выключен'}\n"
        f"Интервал: каждые {user.chronometry_interval_min} мин\n\n"
        f"Выбери интервал или вкл/выкл:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "settings:chrono_toggle")
async def cb_chrono_toggle(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
        if not user:
            return
        new_val = not user.chronometry_enabled
        await update_user_settings(session, callback.from_user.id, chronometry_enabled=new_val)
    await cb_chrono(callback)


@router.callback_query(F.data.startswith("settings:chrono_set:"))
async def cb_chrono_set(callback: CallbackQuery) -> None:
    await callback.answer()
    mins = int(callback.data.split(":")[2])
    async with async_session() as session:
        await update_user_settings(session, callback.from_user.id, chronometry_interval_min=mins)
    await cb_chrono(callback)


# --- Дайджесты вкл/выкл ---

@router.callback_query(F.data == "settings:digest_toggle")
async def cb_digest_toggle(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session:
        user = await get_user(session, callback.from_user.id)
        if not user:
            return
        new_val = not user.digest_enabled
        await update_user_settings(session, callback.from_user.id, digest_enabled=new_val)
    await _show_settings(callback)


@router.message(Command("birthdays"))
async def cmd_birthdays(message: Message) -> None:
    """Показать все дни рождения."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    async with async_session() as session:
        all_bdays = await get_all_birthdays(session, user_id)

    if not all_bdays:
        await message.answer(
            "🎂 Дней рождения пока нет.\n"
            "Скажи мне «запомни день рождения Маши 5 мая» — и я запомню!"
        )
        return

    # Ближайшие
    async with async_session() as session:
        user = await get_user(session, user_id)
    tz = user.timezone if user else "Europe/Moscow"
    today = pendulum.now(tz).date()
    lines = ["🎂 <b>Дни рождения:</b>\n"]
    for b in all_bdays:
        date_str = b.birth_date.strftime("%d.%m")
        age = ""
        if b.year_known:
            years = today.year - b.birth_date.year
            # Если ДР ещё не было в этом году
            this_year_bday = _birthday_in_year(b.birth_date, today.year)
            if this_year_bday <= today:
                years_now = years
            else:
                years_now = years - 1
            age = f" ({years_now} лет)"
        note = f" — {html.escape(b.note)}" if b.note else ""
        lines.append(f"  🎁 {html.escape(b.name)} — {date_str}{age}{note}")

    await _answer_html_parts(message, "\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Экспорт данных в Markdown-файлы."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async with async_session() as session:
            # Задачи
            tasks = await get_user_tasks(session, user_id, status=None)
            if tasks:
                md = "# Задачи\n\n"
                for t in tasks:
                    status = "x" if t.status == "done" else " "
                    frog = " 🐸" if t.is_frog else ""
                    date_str = f" (до {t.due_date.strftime('%d.%m.%Y')})" if t.due_date else ""
                    md += f"- [{status}] {t.title}{frog}{date_str}\n"
                zf.writestr("tasks.md", md)

            # Заметки
            from bot.db.models import Note
            from sqlalchemy import select
            res = await session.execute(
                select(Note).where(Note.user_id == user_id).order_by(Note.created_at.desc())
            )
            notes = list(res.scalars().all())
            if notes:
                md = "# Заметки\n\n"
                for n in notes:
                    title = n.title or "Без названия"
                    date_str = n.created_at.strftime("%Y-%m-%d %H:%M")
                    md += f"## {title}\n*{date_str}*\n\n{n.content}\n\n---\n\n"
                zf.writestr("notes.md", md)

            # Дневник
            from bot.db.models import DiaryEntry
            res = await session.execute(
                select(DiaryEntry).where(DiaryEntry.user_id == user_id)
                .order_by(DiaryEntry.entry_date.desc())
            )
            diary = list(res.scalars().all())
            if diary:
                md = "# Дневник\n\n"
                for d in diary:
                    md += f"## {d.entry_date.strftime('%Y-%m-%d')}\n\n{d.content}\n\n---\n\n"
                zf.writestr("diary.md", md)

            # Мемуарник
            memoirs = await get_memoir_entries(session, user_id, limit=365)
            if memoirs:
                md = "# Мемуарник\n\n"
                for m in memoirs:
                    tag = f" [{m.value_tag}]" if m.value_tag else ""
                    md += f"## {m.event_date.strftime('%Y-%m-%d')}{tag}\n\n{m.content}\n\n---\n\n"
                zf.writestr("memoir.md", md)

            # Дни рождения
            bdays = await get_all_birthdays(session, user_id)
            if bdays:
                md = "# Дни рождения\n\n"
                for b in bdays:
                    note = f" — {b.note}" if b.note else ""
                    md += f"- {b.name}: {b.birth_date.strftime('%d.%m.%Y')}{note}\n"
                zf.writestr("birthdays.md", md)

    buf.seek(0)

    from aiogram.types import BufferedInputFile
    doc = BufferedInputFile(
        buf.read(),
        filename=f"export_{pendulum.now().format('YYYY-MM-DD')}.zip",
    )
    await message.answer_document(doc, caption="📦 Экспорт данных в Markdown (Obsidian-совместимый)")
