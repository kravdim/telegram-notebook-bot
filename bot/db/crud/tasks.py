"""CRUD-операции для задач."""

import re
import uuid
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import List, Optional

import pendulum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from bot.db.models import Task


def normalize_task_identity(title: str) -> str:
    """Нормализовать заголовок для поиска дублей и безопасных мутаций."""
    value = (title or "").casefold().replace("ё", "е")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^0-9a-zа-я_-]+", " ", value)
    value = re.sub(
        r"^(?:(?:надо|нужно|нужна|нужен|купить|сделать|создать|задача)\s+)+",
        "",
        value,
    )
    return " ".join(value.split())


def task_title_similarity(query: str, title: str) -> float:
    """Сходство без завышения оценки из-за общего служебного префикса."""
    left = normalize_task_identity(query)
    right = normalize_task_identity(title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


class ConcurrentTaskUpdateError(RuntimeError):
    """The task changed after it was loaded; caller should retry explicitly."""


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except StaleDataError as exc:
        await session.rollback()
        raise ConcurrentTaskUpdateError(
            "task was changed by another request"
        ) from exc


async def create_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    category: str = "work",
    priority: str = "normal",
    commit: bool = True,
    **kwargs,
) -> Task:
    """Создать задачу."""
    task = Task(
        user_id=user_id,
        title=title,
        category=category,
        priority=priority,
        **kwargs,
    )
    session.add(task)
    if commit:
        await _commit(session)
    else:
        await session.flush()
    await session.refresh(task)
    return task


async def get_task_by_id(
    session: AsyncSession,
    task_id: uuid.UUID,
) -> Optional[Task]:
    """Получить задачу по ID."""
    result = await session.execute(
        select(Task).where(Task.id == task_id)
    )
    return result.scalar_one_or_none()


async def get_user_tasks(
    session: AsyncSession,
    user_id: int,
    status: Optional[str] = "open",
) -> List[Task]:
    """Получить задачи пользователя по статусу. status=None — все задачи."""
    query = select(Task).where(Task.user_id == user_id)
    if status is not None:
        query = query.where(Task.status == status)
    result = await session.execute(query.order_by(Task.created_at.desc()))
    return list(result.scalars().all())


async def get_today_tasks(
    session: AsyncSession,
    user_id: int,
    today: date,
) -> List[Task]:
    """Получить задачи плана дня.

    scheduled_date — дата, на которую задача запланирована.
    due_date остаётся дедлайном. Задачи без дат больше не попадают в каждый день
    автоматически; это бэклог, пока пользователь не запланирует их.
    """
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == "open",
        )
        .order_by(
            Task.is_frog.desc(),  # лягушки первые
            Task.priority.asc(),  # high < medium < normal
            Task.scheduled_date.asc().nullslast(),
            Task.due_date.asc().nullslast(),
            Task.created_at.asc(),
        )
    )
    tasks = list(result.scalars().all())
    today_tasks = []
    for t in tasks:
        if t.is_frog:
            today_tasks.append(t)
            continue

        plan_date = t.scheduled_date or t.due_date
        if plan_date and plan_date <= today:
            today_tasks.append(t)
    return today_tasks


async def get_frog(
    session: AsyncSession,
    user_id: int,
) -> Optional[Task]:
    """Получить текущую лягушку (open, is_frog=True)."""
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.is_frog.is_(True), Task.status == "open")
        .limit(1)
    )
    return result.scalar_one_or_none()


async def set_frog(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
    commit: bool = True,
) -> Optional[Task]:
    """Назначить задачу лягушкой. Снимает флаг с предыдущей."""
    # Снять флаг с текущей лягушки
    current_frog = await get_frog(session, user_id)
    if current_frog:
        current_frog.is_frog = False

    task = await get_task_by_id(session, task_id)
    if not task or task.user_id != user_id:
        return None

    task.is_frog = True
    if commit:
        await _commit(session)
    else:
        await session.flush()
    await session.refresh(task)
    return task


async def search_tasks(
    session: AsyncSession,
    user_id: int,
    query: str,
    status: Optional[str] = None,
) -> List[Task]:
    """Поиск задач по текстовому запросу (ILIKE).

    Сортирует: open задачи первыми, потом по дате создания (новые первые).
    status='open' — только открытые.
    """
    from sqlalchemy import case, func, or_

    normalized = (query or "").strip().casefold().replace("ё", "е")
    if not normalized:
        return []
    normalized_title = func.replace(func.lower(Task.title), "ё", "е")
    pattern = f"%{normalized}%"
    similarity = func.similarity(normalized_title, normalized)
    q = (
        select(Task)
        .where(
            Task.user_id == user_id,
            or_(normalized_title.ilike(pattern), similarity >= 0.45),
        )
    )
    if status:
        q = q.where(Task.status == status)

    # Открытые задачи первыми
    q = q.order_by(
        case((normalized_title == normalized, 0), else_=1),
        case((Task.status == "open", 0), else_=1),
        similarity.desc(),
        Task.created_at.desc(),
    ).limit(10)

    result = await session.execute(q)
    return list(result.scalars().all())


async def update_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
    commit: bool = True,
    **updates,
) -> Optional[Task]:
    """Обновить задачу."""
    task = await get_task_by_id(session, task_id)
    if not task or task.user_id != user_id:
        return None

    if {"status", "resolution", "completed_at"}.intersection(updates):
        raise ValueError("Lifecycle fields require update_task_workflow")
    for key, value in updates.items():
        if hasattr(task, key):
            setattr(task, key, value)
    if commit:
        await _commit(session)
    else:
        await session.flush()
    await session.refresh(task)
    return task


async def get_completed_today(
    session: AsyncSession,
    user_id: int,
    today: date,
    tz: str = "Europe/Moscow",
) -> List[Task]:
    """Получить задачи, выполненные сегодня."""
    start_of_day = pendulum.datetime(today.year, today.month, today.day, tz=tz)
    end_of_day = start_of_day.add(days=1)

    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == "done",
            Task.completed_at >= start_of_day,
            Task.completed_at < end_of_day,
        )
        .order_by(Task.completed_at.asc())
    )
    return list(result.scalars().all())


async def get_completed_in_range(
    session: AsyncSession,
    user_id: int,
    start: datetime,
    end: datetime,
) -> List[Task]:
    """Получить выполненные задачи за период."""
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == "done",
            Task.completed_at >= start,
            Task.completed_at < end,
        )
        .order_by(Task.completed_at.asc())
    )
    return list(result.scalars().all())


async def get_frogs_in_range(
    session: AsyncSession,
    user_id: int,
    start: datetime,
    end: datetime,
) -> List[Task]:
    """Получить лягушки за период (выполненные и невыполненные)."""
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.is_frog.is_(True),
            Task.created_at >= start,
            Task.created_at < end,
        )
        .order_by(Task.created_at.asc())
    )
    return list(result.scalars().all())


async def count_similar_completed(
    session: AsyncSession,
    user_id: int,
    title: str,
) -> tuple[int, Optional[datetime]]:
    """Подсчитать похожие выполненные задачи. Возвращает (count, last_completed_at).

    Ищет по ключевым словам из заголовка (ILIKE по каждому слову длиннее 3 символов).
    """
    # Извлекаем значимые слова. Общие глаголы вроде "настроить" не должны
    # превращать разные задачи в "повторяющиеся".
    stop_words = {
        "надо", "нужно", "сделать", "настроить", "написать", "купить",
        "решить", "разобраться", "заплатить", "выплатить", "оплатить",
    }
    words = [w for w in title.lower().split() if len(w) > 3 and w not in stop_words]
    if not words:
        return 0, None

    from sqlalchemy import func, or_

    # Ищем задачи, содержащие хотя бы одно ключевое слово
    conditions = [func.lower(Task.title).contains(w) for w in words[:4]]

    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == "done",
            or_(*conditions),
        )
        .order_by(Task.completed_at.desc().nullslast())
    )
    tasks = list(result.scalars().all())
    min_overlap = min(2, len(words))
    tasks = [
        task for task in tasks
        if sum(1 for w in words if w in task.title.lower()) >= min_overlap
    ]

    if not tasks:
        return 0, None

    last_at = tasks[0].completed_at if tasks else None
    return len(tasks), last_at


async def delete_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
) -> bool:
    """Удалить задачу. Возвращает True если удалена."""
    task = await get_task_by_id(session, task_id)
    if not task or task.user_id != user_id:
        return False

    await session.delete(task)
    await _commit(session)
    return True
