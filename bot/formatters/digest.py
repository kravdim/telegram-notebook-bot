"""Форматирование утреннего и вечернего дайджестов."""

from datetime import date
from typing import List, Optional

from bot.db.models import Task, Project


_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "normal": "⚪"}
_WEEKDAYS_RU = {
    0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
    4: "пятница", 5: "суббота", 6: "воскресенье",
}


def format_morning_digest(
    today: date,
    tasks: List[Task],
    frog: Optional[Task],
    projects: List[Project],
    project_progress: dict,
    is_weekend: bool,
    active_trip: Optional[str] = None,
) -> str:
    """Форматирование утреннего дайджеста."""
    weekday = _WEEKDAYS_RU.get(today.weekday(), "")
    header = f"☀️ <b>Доброе утро!</b> {today.strftime('%d.%m')} ({weekday})"

    if active_trip:
        header += f"\n✈️ Командировка: {active_trip}"

    parts = [header]

    # Лягушка
    if frog and not is_weekend:
        parts.append(f"\n🐸 <b>Лягушка дня:</b> {frog.title}")
        parts.append("Съешь её первой!")

    # Задачи на сегодня
    today_tasks = [t for t in tasks if t.due_date and t.due_date <= today]
    no_date_tasks = [t for t in tasks if not t.due_date and not t.is_frog]

    if is_weekend:
        # В выходные — только personal + напоминания
        today_tasks = [t for t in today_tasks if t.category == "personal"]
        no_date_tasks = []

    if today_tasks:
        parts.append("\n📋 <b>Задачи на сегодня:</b>")
        for t in today_tasks:
            emoji = _PRIORITY_EMOJI.get(t.priority, "⚪")
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
            parts.append(f"  {emoji} {t.title}{time_str}")

    if no_date_tasks and not is_weekend:
        overdue = [t for t in no_date_tasks if t.due_date and t.due_date < today]
        if overdue:
            parts.append(f"\n⚠️ Просроченных: {len(overdue)}")

    # Слоны
    if projects and not is_weekend:
        parts.append("\n🐘 <b>Слоны:</b>")
        for p in projects[:3]:
            progress = project_progress.get(str(p.id), {})
            pct = progress.get("percent", 0)
            bar = _progress_bar(pct)
            parts.append(f"  {p.title} {bar} {pct}%")

    if not tasks and not frog:
        parts.append("\n🎉 Сегодня свободный день! Отдыхай или запланируй что-нибудь.")

    return "\n".join(parts)


def format_evening_digest(
    today: date,
    completed_tasks: List[Task],
    remaining_tasks: List[Task],
    frog_done: bool,
    frog_title: Optional[str] = None,
) -> str:
    """Форматирование вечернего дайджеста."""
    parts = [f"🌙 <b>Итоги дня</b> ({today.strftime('%d.%m')})"]

    # Лягушка
    if frog_title:
        if frog_done:
            parts.append(f"\n🐸✅ Лягушка «{frog_title}» съедена!")
        else:
            parts.append(f"\n🐸❌ Лягушка «{frog_title}» не съедена")

    # Выполненные
    if completed_tasks:
        parts.append(f"\n✅ <b>Выполнено: {len(completed_tasks)}</b>")
        for t in completed_tasks:
            parts.append(f"  • {t.title}")

    # Невыполненные
    if remaining_tasks:
        parts.append(f"\n📌 <b>Осталось: {len(remaining_tasks)}</b>")
        for t in remaining_tasks:
            emoji = _PRIORITY_EMOJI.get(t.priority, "⚪")
            parts.append(f"  {emoji} {t.title}")

    if not completed_tasks and not remaining_tasks:
        parts.append("\nСегодня задач не было.")

    return "\n".join(parts)


def _progress_bar(percent: int, width: int = 8) -> str:
    """Текстовый прогресс-бар."""
    filled = int(width * percent / 100)
    return "▓" * filled + "░" * (width - filled)
