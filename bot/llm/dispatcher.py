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
from bot.db.crud.tasks import (
    count_similar_completed, create_task, complete_task, get_frog,
    get_today_tasks, get_user_tasks, search_tasks,
    update_task as crud_update_task,
)
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
        elif name == "list_tasks":
            return await _handle_list_tasks(user_id, args, user_timezone)
        elif name == "get_advice":
            return await _handle_get_advice(user_id, args)
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
        from html import escape
        return f"Произошла ошибка при обработке. Попробуй ещё раз."


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

        # Проверяем, повторяющаяся ли это задача
        similar_count, last_at = await count_similar_completed(session, user_id, title)

    parts = [f"Задача создана: {task.title}"]
    if due_date:
        parts.append(f"📅 {due_date.strftime('%d.%m.%Y')}")
    if due_time:
        parts.append(f"⏰ {due_time.strftime('%H:%M')}")
    if remind_at:
        parts.append(f"🔔 Напомню: {remind_at.strftime('%d.%m %H:%M')}")
    if is_frog:
        parts.append("🐸 Лягушка!")

    result = " ".join(parts) + " ✅"

    # Комментарий для повторяющихся задач
    if similar_count >= 2:
        result += "\n" + _recurring_create_comment(title, similar_count, last_at, tz)

    return result


async def _handle_complete_task(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "")
    async with async_session() as session:
        task = await complete_task(session, user_id, query)
        if not task:
            return "Не нашёл такую задачу. Уточни название."

        # Считаем сколько раз подобное уже выполнялось (включая текущий)
        similar_count, _ = await count_similar_completed(session, user_id, task.title)

    result = f"Задача «{task.title}» выполнена! 🎉"

    if similar_count >= 2:
        result += "\n" + _recurring_complete_comment(task.title, similar_count)

    return result


def _recurring_create_comment(title: str, count: int, last_at, tz: str) -> str:
    """Комментарий при создании повторяющейся задачи."""
    import random

    if last_at:
        last_local = pendulum.instance(last_at).in_tz(tz)
        days_ago = (pendulum.now(tz) - last_local).in_days()
    else:
        days_ago = None

    comments = [
        f"🔄 Знакомая задача! Ты уже делал подобное {count} раз.",
        f"🔄 О, снова! Это уже {count}-й раз — настоящая рутина 💪",
        f"🔄 Похоже, это регулярное дело (выполнено {count} раз). Ты профи!",
        f"🔄 Уже {count}-й раз! Может, стоит сделать повторяющуюся задачу? 😉",
    ]

    if count >= 10:
        comments.extend([
            f"🔄 {count} раз! Это уже традиция 🏆",
            f"🔄 Ветеран! {count}-е выполнение этой задачи.",
        ])

    if days_ago is not None and days_ago <= 1:
        comments.append("🔄 Только вчера делал — и снова! Вот это темп 🚀")

    result = random.choice(comments)

    if days_ago is not None and days_ago > 0:
        result += f"\n📊 Последний раз: {days_ago} дн. назад"

    return result


def _recurring_complete_comment(title: str, count: int) -> str:
    """Комментарий при завершении повторяющейся задачи."""
    import random

    if count <= 3:
        comments = [
            f"📊 Это уже {count}-й раз! Начинается традиция.",
            f"📊 {count}-е выполнение. Входишь в ритм!",
        ]
    elif count <= 10:
        comments = [
            f"📊 {count}-й раз! Стабильность — признак мастерства 💪",
            f"📊 Уже {count} раз — ты машина! 🤖",
            f"📊 {count}-е выполнение. Рутина? Нет, дисциплина! 💪",
        ]
    else:
        comments = [
            f"📊 {count}-й раз! Легенда! 🏆",
            f"📊 {count} выполнений — впечатляет! Настоящий профессионал.",
            f"📊 Ого, уже {count}! Это дело — часть твоей жизни 😄",
        ]

    return random.choice(comments)


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


async def _handle_list_tasks(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    scope = args.get("scope", "today")
    today = pendulum.now(tz).date()

    async with async_session() as session:
        if scope == "today":
            tasks = await get_today_tasks(session, user_id, today)
            frog = await get_frog(session, user_id)
            if not tasks:
                return "На сегодня задач нет. Свободный день! 🎉"

            lines = ["📋 Задачи на сегодня:\n"]
            for t in tasks:
                icon = "🐸" if t.is_frog else ("🔴" if t.priority == "high" else "📌")
                time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
                date_str = ""
                if t.due_date and t.due_date < today:
                    date_str = f" ⚠️ просрочена с {t.due_date.strftime('%d.%m')}"
                lines.append(f"{icon} {t.title}{time_str}{date_str}")
            return "\n".join(lines)

        elif scope == "done_today":
            from bot.db.crud.tasks import get_completed_today
            completed = await get_completed_today(session, user_id, today, tz)
            if not completed:
                return "Сегодня пока ничего не выполнено."
            lines = [f"✅ Выполнено сегодня: {len(completed)}\n"]
            for t in completed:
                lines.append(f"  • {t.title}")
            return "\n".join(lines)

        elif scope == "overdue":
            all_tasks = await get_user_tasks(session, user_id, status="open")
            overdue = [t for t in all_tasks if t.due_date and t.due_date < today]
            if not overdue:
                return "Просроченных задач нет 👍"
            lines = ["⚠️ Просроченные задачи:\n"]
            for t in overdue:
                lines.append(f"📌 {t.title} (дедлайн {t.due_date.strftime('%d.%m')})")
            return "\n".join(lines)

        else:  # all
            tasks = await get_user_tasks(session, user_id, status="open")
            if not tasks:
                return "Открытых задач нет. Всё сделано! 🎉"
            lines = ["📋 Все открытые задачи:\n"]
            for t in tasks:
                icon = "🐸" if t.is_frog else ("🔴" if t.priority == "high" else "📌")
                date_str = f" 📅 {t.due_date.strftime('%d.%m')}" if t.due_date else ""
                lines.append(f"{icon} {t.title}{date_str}")
            return "\n".join(lines)


async def _handle_search(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("query", "")
    scope = args.get("scope", "all")
    results = []

    from bot.embeddings.indexer import get_embedding
    query_emb = await get_embedding(query)

    async with async_session() as session:
        # Поиск по задачам
        if scope in ("all", "tasks"):
            tasks = await search_tasks(session, user_id, query)
            for t in tasks[:5]:
                status = "✅" if t.status == "done" else "📌"
                results.append(f"{status} {t.title}")

        # Поиск по заметкам (гибридный)
        if scope in ("all", "notes"):
            from sqlalchemy import select, text
            from bot.db.models import Note
            if query_emb:
                res = await session.execute(
                    text("""
                        SELECT id, title, content,
                               COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0) * 0.6 +
                               COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                        FROM notes
                        WHERE user_id = :uid
                          AND (content % CAST(:query AS text) OR content ILIKE :pattern
                               OR (embedding IS NOT NULL AND embedding <=> CAST(:emb AS vector) < 0.8))
                        ORDER BY score DESC
                        LIMIT 5
                    """),
                    {"uid": user_id, "query": query, "pattern": f"%{query}%", "emb": str(query_emb)},
                )
            else:
                res = await session.execute(
                    select(Note)
                    .where(Note.user_id == user_id, Note.content.ilike(f"%{query}%"))
                    .limit(5)
                )
            for row in res.fetchall():
                title = row.title or (row.content[:50] if hasattr(row, "content") else str(row)[:50])
                results.append(f"📝 {title}")

        # Поиск по дневнику (гибридный)
        if scope in ("all", "diary"):
            from sqlalchemy import text
            from bot.db.models import DiaryEntry
            if query_emb:
                res = await session.execute(
                    text("""
                        SELECT id, content,
                               COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0) * 0.6 +
                               COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                        FROM diary_entries
                        WHERE user_id = :uid
                          AND (content % CAST(:query AS text) OR content ILIKE :pattern
                               OR (embedding IS NOT NULL AND embedding <=> CAST(:emb AS vector) < 0.8))
                        ORDER BY score DESC
                        LIMIT 5
                    """),
                    {"uid": user_id, "query": query, "pattern": f"%{query}%", "emb": str(query_emb)},
                )
            else:
                from sqlalchemy import select
                res = await session.execute(
                    select(DiaryEntry)
                    .where(DiaryEntry.user_id == user_id, DiaryEntry.content.ilike(f"%{query}%"))
                    .limit(5)
                )
            for row in res.fetchall():
                content = row.content if hasattr(row, "content") else str(row)
                results.append(f"📓 {content[:50]}...")

        # Поиск по мемуарнику (гибридный)
        if scope in ("all", "memoir"):
            from sqlalchemy import text
            from bot.db.models import MemoirEntry
            if query_emb:
                res = await session.execute(
                    text("""
                        SELECT id, content,
                               COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0) * 0.6 +
                               COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                        FROM memoir_entries
                        WHERE user_id = :uid
                          AND (content % CAST(:query AS text) OR content ILIKE :pattern
                               OR (embedding IS NOT NULL AND embedding <=> CAST(:emb AS vector) < 0.8))
                        ORDER BY score DESC
                        LIMIT 5
                    """),
                    {"uid": user_id, "query": query, "pattern": f"%{query}%", "emb": str(query_emb)},
                )
            else:
                from sqlalchemy import select
                res = await session.execute(
                    select(MemoirEntry)
                    .where(MemoirEntry.user_id == user_id, MemoirEntry.content.ilike(f"%{query}%"))
                    .limit(5)
                )
            for row in res.fetchall():
                content = row.content if hasattr(row, "content") else str(row)
                results.append(f"📔 {content[:50]}...")

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


async def _handle_get_advice(user_id: int, args: Dict[str, Any]) -> str:
    """Поиск совета в базе знаний Архангельского."""
    query = args.get("query", "").strip()
    if not query:
        return "Задай вопрос — я подскажу по методике Архангельского."

    from bot.db.crud.knowledge import hybrid_search
    from bot.embeddings.indexer import get_embedding

    query_emb = await get_embedding(query)

    async with async_session() as session:
        chunks = await hybrid_search(
            session, query, query_embedding=query_emb, limit=3,
        )

    if not chunks:
        return (
            "Не нашёл подходящего совета в базе знаний. "
            "Попробуй переформулировать вопрос, например: "
            "«Как справиться с прокрастинацией?» или «Как декомпозировать слона?»"
        )

    # Формируем ответ из найденных чанков
    parts = ["📚 <b>Совет по методике Архангельского:</b>\n"]
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"{chunk.content}")
        if i < len(chunks):
            parts.append("")  # пустая строка между чанками

    parts.append(f"\n📖 <i>Источник: {chunks[0].source}</i>")
    return "\n".join(parts)
