"""Исполнение function calls от LLM: валидация + CRUD + ответ."""

import json
import logging
from datetime import date, datetime, time
from typing import Any, Dict, Optional, Tuple

import pendulum
from json_repair import repair_json

from bot.db.crud.diary import create_diary_entry
from bot.db.crud.notes import create_note
from bot.db.crud.projects import create_project as crud_create_project
from bot.db.crud.reminders import create_reminder
from bot.db.crud.tasks import create_task, complete_task, search_tasks, update_task as crud_update_task
from bot.db.engine import async_session

logger = logging.getLogger(__name__)


def parse_function_call(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Парсинг function call с json_repair."""
    name = raw.get("name", "")
    args_raw = raw.get("arguments", "{}")

    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            logger.warning("Невалидный JSON от LLM, применяю json_repair")
            repaired = repair_json(args_raw)
            args = json.loads(repaired)
    else:
        args = args_raw

    return name, args


async def dispatch(
    function_call: Dict[str, Any],
    user_id: int,
    user_timezone: str = "Europe/Moscow",
) -> str:
    """Исполнить function call и вернуть текст ответа для пользователя."""
    name, args = parse_function_call(function_call)

    try:
        if name == "create_task":
            return await _handle_create_task(user_id, args, user_timezone)
        elif name == "complete_task":
            return await _handle_complete_task(user_id, args)
        elif name == "create_note":
            return await _handle_create_note(user_id, args)
        elif name == "create_diary_entry":
            return await _handle_create_diary(user_id, args)
        elif name == "create_reminder":
            return await _handle_create_reminder(user_id, args, user_timezone)
        elif name == "respond_to_user":
            return args.get("message", "")
        elif name == "search":
            return await _handle_search(user_id, args)
        elif name == "update_task":
            return await _handle_update_task(user_id, args)
        elif name == "delete_task":
            return await _handle_delete_task(user_id, args)
        elif name == "create_project":
            return await _handle_create_project(user_id, args)
        else:
            logger.warning("Неизвестная функция: %s", name)
            return "Не удалось обработать команду. Попробуй переформулировать."
    except Exception as e:
        logger.error("Ошибка при выполнении %s: %s", name, e, exc_info=True)
        return f"Произошла ошибка: {e}"


def _validate_title(title: str) -> Optional[str]:
    """Валидация заголовка."""
    if not title or not title.strip():
        return "Заголовок не может быть пустым."
    if len(title) > 500:
        return "Заголовок слишком длинный (макс. 500 символов)."
    return None


def _parse_date(date_str: Optional[str], tz: str) -> Optional[date]:
    """Парсинг даты через pendulum."""
    if not date_str:
        return None
    try:
        dt = pendulum.parse(date_str, tz=tz)
        return dt.date()
    except Exception:
        return None


def _parse_time(time_str: Optional[str]) -> Optional[time]:
    """Парсинг времени HH:MM."""
    if not time_str:
        return None
    try:
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return None


def _parse_datetime(dt_str: Optional[str], tz: str) -> Optional[datetime]:
    """Парсинг ISO datetime через pendulum."""
    if not dt_str:
        return None
    try:
        dt = pendulum.parse(dt_str, tz=tz)
        return dt
    except Exception:
        return None


async def _handle_create_task(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    title = args.get("title", "").strip()
    err = _validate_title(title)
    if err:
        return err

    due_date = _parse_date(args.get("due_date"), tz)
    due_time = _parse_time(args.get("due_time"))
    remind_at = _parse_datetime(args.get("remind_at"), tz)

    # Проверка: due_date не в прошлом
    if due_date and due_date < pendulum.now(tz).date():
        return "Дата дедлайна в прошлом. Уточни дату."

    category = args.get("category", "work")
    if category not in ("work", "personal"):
        category = "work"

    priority = args.get("priority", "normal")
    if priority not in ("high", "medium", "normal"):
        priority = "normal"

    is_frog = args.get("is_frog", False)

    async with async_session() as session:
        task = await create_task(
            session,
            user_id=user_id,
            title=title,
            category=category,
            priority=priority,
            is_frog=is_frog,
            due_date=due_date,
            due_time=due_time,
            remind_at=remind_at,
            remind_before_min=args.get("remind_before_min"),
        )

    parts = [f"Задача создана: {task.title}"]
    if due_date:
        parts.append(f"📅 {due_date.strftime('%d.%m.%Y')}")
    if due_time:
        parts.append(f"⏰ {due_time.strftime('%H:%M')}")
    if remind_at:
        parts.append(f"🔔 Напомню: {remind_at.strftime('%d.%m %H:%M')}")
    if is_frog:
        parts.append("🐸 Лягушка!")
    return " ".join(parts) + " ✅"


async def _handle_complete_task(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "")
    async with async_session() as session:
        task = await complete_task(session, user_id, query)
    if task:
        return f"Задача «{task.title}» выполнена! 🎉"
    return "Не нашёл такую задачу. Уточни название."


async def _handle_create_note(user_id: int, args: Dict[str, Any]) -> str:
    content = args.get("content", "").strip()
    if not content:
        return "Заметка не может быть пустой."

    title = args.get("title")
    tags = args.get("tags", [])

    async with async_session() as session:
        note = await create_note(session, user_id, content=content, title=title, tags=tags)

    return f"Заметка сохранена ✅" + (f" ({note.title})" if note.title else "")


async def _handle_create_diary(user_id: int, args: Dict[str, Any]) -> str:
    content = args.get("content", "").strip()
    if not content:
        return "Запись не может быть пустой."

    async with async_session() as session:
        entry = await create_diary_entry(session, user_id, content=content)

    return "Записано в дневник ✅"


async def _handle_create_reminder(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    message = args.get("message", "").strip()
    if not message:
        return "Текст напоминания не может быть пустым."

    remind_at = _parse_datetime(args.get("remind_at"), tz)
    if not remind_at:
        return "Не удалось распознать время напоминания. Укажи дату и время."

    if remind_at < pendulum.now(tz):
        return "Время напоминания в прошлом. Уточни."

    async with async_session() as session:
        reminder = await create_reminder(
            session, user_id,
            message=message,
            remind_at=remind_at,
            repeat_rule=args.get("repeat_rule"),
        )

    return f"Напоминание установлено: {message}\n🔔 {remind_at.strftime('%d.%m.%Y %H:%M')}"


async def _handle_search(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("query", "")
    scope = args.get("scope", "all")
    results = []

    async with async_session() as session:
        # Поиск по задачам
        if scope in ("all", "tasks"):
            tasks = await search_tasks(session, user_id, query)
            for t in tasks[:5]:
                status = "✅" if t.status == "done" else "📌"
                results.append(f"{status} {t.title}")

        # Поиск по заметкам
        if scope in ("all", "notes"):
            from sqlalchemy import select
            from bot.db.models import Note
            pattern = f"%{query}%"
            res = await session.execute(
                select(Note)
                .where(Note.user_id == user_id, Note.content.ilike(pattern))
                .limit(5)
            )
            for n in res.scalars().all():
                title = n.title or n.content[:50]
                results.append(f"📝 {title}")

        # Поиск по дневнику
        if scope in ("all", "diary"):
            from sqlalchemy import select
            from bot.db.models import DiaryEntry
            pattern = f"%{query}%"
            res = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user_id, DiaryEntry.content.ilike(pattern))
                .limit(5)
            )
            for d in res.scalars().all():
                results.append(f"📓 {d.content[:50]}...")

        # Поиск по мемуарнику
        if scope in ("all", "memoir"):
            from sqlalchemy import select
            from bot.db.models import MemoirEntry
            pattern = f"%{query}%"
            res = await session.execute(
                select(MemoirEntry)
                .where(MemoirEntry.user_id == user_id, MemoirEntry.content.ilike(pattern))
                .limit(5)
            )
            for m in res.scalars().all():
                results.append(f"📔 {m.content[:50]}...")

    if results:
        return f"🔍 По запросу «{query}» найдено:\n" + "\n".join(results)
    return f"По запросу «{query}» ничего не найдено."


async def _handle_update_task(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "")
    updates = args.get("updates", {})

    if not query:
        return "Укажи какую задачу обновить."
    if not updates:
        return "Не указаны изменения."

    async with async_session() as session:
        tasks = await search_tasks(session, user_id, query)
        if not tasks:
            return f"Не нашёл задачу «{query}»."

        task = tasks[0]
        # Фильтруем допустимые поля
        allowed = {"title", "priority", "is_frog", "due_date", "due_time", "status"}
        clean_updates = {k: v for k, v in updates.items() if k in allowed}

        if not clean_updates:
            return "Нет допустимых полей для обновления."

        updated = await crud_update_task(session, task.id, user_id, **clean_updates)

    if updated:
        return f"Задача «{updated.title}» обновлена ✅"
    return "Не удалось обновить задачу."


async def _handle_delete_task(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "")
    if not query:
        return "Укажи какую задачу удалить."

    async with async_session() as session:
        tasks = await search_tasks(session, user_id, query)

    if not tasks:
        return f"Не нашёл задачу «{query}»."

    task = tasks[0]
    # Возвращаем текст с предложением confirm — кнопки добавит message handler
    return f"CONFIRM_DELETE:{task.id}:{task.title}"


async def _handle_create_project(user_id: int, args: Dict[str, Any]) -> str:
    title = args.get("title", "").strip()
    err = _validate_title(title)
    if err:
        return err

    description = args.get("description", "")
    category = args.get("category", "work")
    if category not in ("work", "personal"):
        category = "work"

    async with async_session() as session:
        project = await crud_create_project(
            session,
            user_id=user_id,
            title=title,
            description=description,
            category=category,
        )

    return f"PROJECT_CREATED:{project.id}:{project.title}"
