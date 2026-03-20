"""Многошаговый диалог декомпозиции проектов (слонов) на бифштексы."""

import json
import logging
from typing import List, Optional

from json_repair import repair_json

from bot.db.crud.projects import get_project_by_id, update_project
from bot.db.crud.tasks import create_task
from bot.db.engine import async_session
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

        return [str(t).strip() for t in tasks if t]

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
        for title in task_titles:
            await create_task(
                session,
                user_id=user_id,
                title=title,
                category=category,
                project_id=pid,
            )
            created += 1

        # Обновляем список задач проекта
        project = await get_project_by_id(session, pid)
        if project:
            from bot.db.crud.projects import get_project_tasks
            tasks = await get_project_tasks(session, pid)
            project.task_ids_ordered = [str(t.id) for t in tasks]
            await session.commit()

    return created
