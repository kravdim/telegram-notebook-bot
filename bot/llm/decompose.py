"""Многошаговый диалог декомпозиции проектов (слонов) на бифштексы."""

import json
import logging
from typing import List, Optional

from json_repair import repair_json

from bot.db.crud.projects import get_project_by_id, update_project
from bot.db.engine import async_session
from bot.db.models import Task
from bot.llm.client import LLMClient
from bot.llm.queue import LLMQueue, PRIORITY_DECOMPOSE

logger = logging.getLogger(__name__)


async def decompose_project(
    llm_client: LLMClient,
    llm_queue: LLMQueue,
    user_id: int,
    project_id: str,
    project_title: str,
    project_description: str = "",
) -> List[str]:
    """Декомпозировать проект на задачи через LLM. Возвращает список названий задач."""
    prompt = (
        "Ты — эксперт по декомпозиции проектов.\n"
        f'Проект: "{project_title}"\n'
    )
    if project_description:
        prompt += f'Описание: "{project_description}"\n'

    prompt += (
        "\nРазбей этот проект (слона) на 5-10 конкретных задач (бифштексов).\n"
        "Каждый бифштекс должен быть выполним за 1-2 часа.\n"
        "Первая задача должна быть самой простой — чтобы начать было легко (правило 5 минут).\n"
        "Верни JSON массив строк:\n"
        '["Задача 1", "Задача 2", ...]\n'
        "Только JSON, без пояснений."
    )

    try:
        response = await llm_queue.submit(
            PRIORITY_DECOMPOSE,
            llm_client.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Декомпозируй: {project_title}"},
                ],
            ),
        )

        if not response.content:
            return []

        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0]

        tasks = json.loads(repair_json(content))
        if not isinstance(tasks, list):
            return []

        cleaned = []
        seen = set()
        for item in tasks[:10]:
            title = str(item).strip()[:500]
            key = title.casefold()
            if title and key not in seen:
                cleaned.append(title)
                seen.add(key)
        return cleaned

    except Exception as e:
        logger.error("Ошибка декомпозиции: %s", e)
        return []


async def create_project_tasks(
    user_id: int,
    project_id: str,
    task_titles: List[str],
    category: str = "work",
) -> int:
    """Создать задачи проекта в БД. Возвращает количество созданных."""
    import uuid
    pid = uuid.UUID(project_id)
    created = 0

    async with async_session() as session:
        try:
            project = await get_project_by_id(session, pid)
            if not project or project.user_id != user_id:
                return 0

            for title in task_titles:
                task = Task(
                    user_id=user_id,
                    title=title,
                    category=category,
                    project_id=pid,
                )
                session.add(task)
                created += 1

            await session.commit()
        except Exception:
            await session.rollback()
            logger.error("Ошибка при создании задач проекта %s, откат", project_id, exc_info=True)
            return 0

    return created
