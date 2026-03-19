"""CRUD-операции для задач."""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pendulum
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Task


async def create_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    category: str = "work",
    priority: str = "normal",
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
    await session.commit()
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
    status: str = "open",
) -> List[Task]:
    """Получить задачи пользователя по статусу."""
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.status == status)
        .order_by(Task.created_at.desc())
    )
    return list(result.scalars().all())


async def get_today_tasks(
    session: AsyncSession,
    user_id: int,
    today: date,
) -> List[Task]:
    """Получить задачи на сегодня: due_date = today или лягушки или без даты."""
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == "open",
        )
        .order_by(
            Task.is_frog.desc(),  # лягушки первые
            Task.priority.asc(),  # high < medium < normal
            Task.due_date.asc().nullslast(),
            Task.created_at.asc(),
        )
    )
    tasks = list(result.scalars().all())
    # Фильтруем: due_date == today, или due_date <= today (просроченные), или лягушки
    today_tasks = []
    for t in tasks:
        if t.is_frog:
            today_tasks.append(t)
        elif t.due_date and t.due_date <= today:
            today_tasks.append(t)
        elif not t.due_date:
            today_tasks.append(t)
    return today_tasks


async def get_frog(
    session: AsyncSession,
    user_id: int,
) -> Optional[Task]:
    """Получить текущую лягушку (open, is_frog=True)."""
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.is_frog == True, Task.status == "open")
        .limit(1)
    )
    return result.scalar_one_or_none()


async def set_frog(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
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
    await session.commit()
    await session.refresh(task)
    return task


async def search_tasks(
    session: AsyncSession,
    user_id: int,
    query: str,
) -> List[Task]:
    """Поиск задач по текстовому запросу (ILIKE)."""
    pattern = f"%{query}%"
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.title.ilike(pattern),
        )
        .order_by(Task.created_at.desc())
        .limit(10)
    )
    return list(result.scalars().all())


async def complete_task(
    session: AsyncSession,
    user_id: int,
    search_query: str,
) -> Optional[Task]:
    """Найти задачу по запросу и отметить выполненной."""
    tasks = await search_tasks(session, user_id, search_query)
    if not tasks:
        return None

    task = tasks[0]
    task.status = "done"
    task.resolution = "completed"
    task.completed_at = pendulum.now("UTC")
    await session.commit()
    await session.refresh(task)
    return task


async def complete_task_by_id(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
) -> Optional[Task]:
    """Отметить задачу выполненной по ID."""
    task = await get_task_by_id(session, task_id)
    if not task or task.user_id != user_id:
        return None

    task.status = "done"
    task.resolution = "completed"
    task.completed_at = pendulum.now("UTC")
    await session.commit()
    await session.refresh(task)
    return task


async def update_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
    **updates,
) -> Optional[Task]:
    """Обновить задачу."""
    task = await get_task_by_id(session, task_id)
    if not task or task.user_id != user_id:
        return None

    for key, value in updates.items():
        if hasattr(task, key):
            setattr(task, key, value)

    await session.commit()
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
    await session.commit()
    return True
