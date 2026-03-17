"""Форматирование вечернего разбора невыполненных задач."""

from typing import List

from bot.db.models import Task


def format_evening_review(tasks: List[Task]) -> str:
    """Форматирование списка невыполненных задач для разбора."""
    if not tasks:
        return ""

    parts = [
        "📝 <b>Разбор невыполненных задач</b>\n",
        "Что делаем с каждой?",
    ]
    for i, task in enumerate(tasks[:10], 1):
        parts.append(f"\n{i}. {task.title}")

    parts.append(
        "\nДля каждой задачи выбери действие кнопкой ниже:"
    )
    return "\n".join(parts)
