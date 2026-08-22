"""CRUD-операции для проектов (слонов)."""

import re
import uuid
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Project, Task


async def create_project(
    session: AsyncSession,
    user_id: int,
    title: str,
    description: Optional[str] = None,
    category: str = "work",
) -> Project:
    """Создать проект."""
    project = Project(
        user_id=user_id,
        title=title,
        description=description,
        category=category,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def get_project_by_id(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> Optional[Project]:
    """Получить проект по ID."""
    result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


async def get_user_projects(
    session: AsyncSession,
    user_id: int,
    status: str = "active",
) -> List[Project]:
    """Получить проекты пользователя."""
    result = await session.execute(
        select(Project)
        .where(Project.user_id == user_id, Project.status == status)
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


async def search_projects(
    session: AsyncSession,
    user_id: int,
    query: str,
    status: Optional[str] = "active",
) -> List[Project]:
    """Поиск проектов пользователя по названию."""
    stripped_query = re.sub(
        r"^[А-ЯЁA-Z]\d{1,4}-", "", (query or "").strip(), flags=re.IGNORECASE
    ).strip()
    patterns = [Project.title.ilike(f"%{query}%")]
    if stripped_query and stripped_query.casefold() != query.casefold():
        patterns.append(Project.title.ilike(f"%{stripped_query}%"))
    q = select(Project).where(Project.user_id == user_id, or_(*patterns))
    if status is not None:
        q = q.where(Project.status == status)
    result = await session.execute(q.order_by(Project.created_at.desc()).limit(10))
    return list(result.scalars().all())


async def complete_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    user_id: int,
) -> Optional[Project]:
    """Отметить проект завершённым."""
    project = await get_project_by_id(session, project_id)
    if not project or project.user_id != user_id:
        return None
    project.status = "done"
    await session.commit()
    await session.refresh(project)
    return project


async def complete_project_and_cancel_open_tasks(
    session: AsyncSession,
    project_id: uuid.UUID,
    user_id: int,
) -> Optional[Project]:
    """Закрыть проект и атомарно отменить его незавершённые задачи."""
    project = await get_project_by_id(session, project_id)
    if not project or project.user_id != user_id or project.status != "active":
        return None
    tasks = await get_project_tasks(session, project_id)
    for task in tasks:
        if task.status == "open":
            task.status = "cancelled"
            task.resolution = "cancelled"
    project.status = "done"
    await session.commit()
    await session.refresh(project)
    return project


async def get_project_tasks(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> List[Task]:
    """Получить задачи проекта."""
    result = await session.execute(
        select(Task)
        .where(Task.project_id == project_id)
        .order_by(Task.created_at.asc())
    )
    return list(result.scalars().all())


async def update_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    user_id: int,
    **updates,
) -> Optional[Project]:
    """Обновить проект."""
    project = await get_project_by_id(session, project_id)
    if not project or project.user_id != user_id:
        return None
    for key, value in updates.items():
        if hasattr(project, key):
            setattr(project, key, value)
    await session.commit()
    await session.refresh(project)
    return project


async def get_project_progress(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Получить прогресс проекта."""
    tasks = await get_project_tasks(session, project_id)
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    return {
        "total": total,
        "done": done,
        "percent": int(done / total * 100) if total > 0 else 0,
    }


async def batch_project_progress(
    session: AsyncSession,
    project_ids: list,
) -> dict:
    """Получить прогресс для нескольких проектов одним запросом."""
    if not project_ids:
        return {}

    from sqlalchemy import func, case
    result = await session.execute(
        select(
            Task.project_id,
            func.count().label("total"),
            func.sum(case((Task.status == "done", 1), else_=0)).label("done"),
        )
        .where(Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
    )

    progress = {}
    for row in result.all():
        total = row.total or 0
        done = row.done or 0
        progress[row.project_id] = {
            "total": total,
            "done": done,
            "percent": int(done / total * 100) if total > 0 else 0,
        }

    # Проекты без задач
    for pid in project_ids:
        if pid not in progress:
            progress[pid] = {"total": 0, "done": 0, "percent": 0}

    return progress
