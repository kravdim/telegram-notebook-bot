"""Форматирование утреннего и вечернего дайджестов."""

from datetime import date
from html import escape
from typing import List, Optional

from bot.db.models import Project, Task

_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "normal": "⚪"}
_WEEKDAYS_RU = {
    0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
    4: "пятница", 5: "суббота", 6: "воскресенье",
}


def _append_birthdays(parts: list[str], today: date, birthdays: list) -> None:
    if not birthdays:
        return
    parts.append("\n🎂 <b>Сегодня день рождения:</b>")
    for birthday in birthdays:
        age = ""
        if getattr(birthday, "year_known", birthday.birth_date.year > 1900):
            age = f" ({today.year - birthday.birth_date.year} лет)"
        note = f" — {escape(birthday.note)}" if birthday.note else ""
        parts.append(f"  🎁 {escape(birthday.name)}{age}{note}")
    parts.append("  Не забудь поздравить!")


def _append_tasks(parts: list[str], tasks: List[Task]) -> None:
    if not tasks:
        return
    parts.append("\n📋 <b>Задачи на сегодня:</b>")
    for task in tasks:
        emoji = _PRIORITY_EMOJI.get(task.priority, "⚪")
        time_text = f" ⏰ {task.due_time.strftime('%H:%M')}" if task.due_time else ""
        parts.append(f"  {emoji} {escape(task.title)}{time_text}")


def _overdue_count(today: date, tasks: List[Task]) -> int:
    count = 0
    for task in tasks:
        scheduled_date = getattr(task, "scheduled_date", None) or task.due_date
        if scheduled_date is not None and scheduled_date < today and task.status == "open":
            count += 1
    return count


def _append_projects(
    parts: list[str], projects: List[Project], project_progress: dict
) -> None:
    if not projects:
        return
    parts.append("\n🐘 <b>Слоны:</b>")
    for project in projects[:3]:
        percent = project_progress.get(str(project.id), {}).get("percent", 0)
        parts.append(
            f"  {escape(project.title)} {_progress_bar(percent)} {percent}%"
        )


def _append_empty_day(
    parts: list[str], *, tasks: list, frog, projects: list, has_context: bool
) -> None:
    if tasks or frog:
        return
    if projects:
        parts.append("\n📋 Задач на сегодня нет. Можно взять один маленький шаг по слону.")
    elif has_context:
        parts.append("\n📋 Задач на сегодня нет.")
    else:
        parts.append("\n🎉 Сегодня свободный день! Отдыхай или запланируй что-нибудь.")


def format_morning_digest(
    today: date,
    tasks: List[Task],
    frog: Optional[Task],
    projects: List[Project],
    project_progress: dict,
    is_weekend: bool,
    active_trip: Optional[str] = None,
    birthdays: Optional[List] = None,
) -> str:
    """Форматирование утреннего дайджеста."""
    visible_frog = None if is_weekend else frog
    visible_projects = [] if is_weekend else projects
    weekday = _WEEKDAYS_RU.get(today.weekday(), "")
    header = f"☀️ <b>Доброе утро!</b> {today.strftime('%d.%m')} ({weekday})"

    if active_trip:
        header += f"\n✈️ Командировка: {escape(active_trip)}"

    parts = [header]

    _append_birthdays(parts, today, birthdays or [])
    if visible_frog:
        parts.append(f"\n🐸 <b>Лягушка дня:</b> {escape(visible_frog.title)}")
        parts.append("Съешь её первой!")
    _append_tasks(parts, tasks)
    if not is_weekend:
        if overdue := _overdue_count(today, tasks):
            parts.append(f"\n⚠️ Просроченных: {overdue}")
    _append_projects(parts, visible_projects, project_progress)
    _append_empty_day(
        parts,
        tasks=tasks,
        frog=visible_frog,
        projects=visible_projects,
        has_context=bool(active_trip or birthdays),
    )

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
            parts.append(f"\n🐸✅ Лягушка «{escape(frog_title)}» съедена!")
        else:
            parts.append(f"\n🐸❌ Лягушка «{escape(frog_title)}» не съедена")

    # Выполненные
    if completed_tasks:
        parts.append(f"\n✅ <b>Выполнено: {len(completed_tasks)}</b>")
        for t in completed_tasks:
            parts.append(f"  • {escape(t.title)}")

    # Невыполненные
    if remaining_tasks:
        parts.append(f"\n📌 <b>Осталось: {len(remaining_tasks)}</b>")
        for t in remaining_tasks:
            emoji = _PRIORITY_EMOJI.get(t.priority, "⚪")
            parts.append(f"  {emoji} {escape(t.title)}")

    if not completed_tasks and not remaining_tasks:
        parts.append("\nСегодня задач не было.")

    return "\n".join(parts)


def _progress_bar(percent: int, width: int = 8) -> str:
    """Текстовый прогресс-бар."""
    filled = int(width * percent / 100)
    return "▓" * filled + "░" * (width - filled)
