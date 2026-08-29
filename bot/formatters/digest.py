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
    weekday = _WEEKDAYS_RU.get(today.weekday(), "")
    header = f"☀️ <b>Доброе утро!</b> {today.strftime('%d.%m')} ({weekday})"

    if active_trip:
        header += f"\n✈️ Командировка: {escape(active_trip)}"

    parts = [header]

    # Дни рождения
    if birthdays:
        parts.append("\n🎂 <b>Сегодня день рождения:</b>")
        for b in birthdays:
            age = ""
            if getattr(b, "year_known", b.birth_date.year > 1900):
                years = today.year - b.birth_date.year
                age = f" ({years} лет)"
            note = f" — {escape(b.note)}" if b.note else ""
            parts.append(f"  🎁 {escape(b.name)}{age}{note}")
        parts.append("  Не забудь поздравить!")

    # Лягушка
    if frog and not is_weekend:
        parts.append(f"\n🐸 <b>Лягушка дня:</b> {escape(frog.title)}")
        parts.append("Съешь её первой!")

    if tasks:
        parts.append("\n📋 <b>Задачи на сегодня:</b>")
        for t in tasks:
            emoji = _PRIORITY_EMOJI.get(t.priority, "⚪")
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
            parts.append(f"  {emoji} {escape(t.title)}{time_str}")

    if not is_weekend:
        overdue = []
        for task in tasks:
            plan_date = getattr(task, "scheduled_date", None) or task.due_date
            if plan_date is not None and plan_date < today and task.status == "open":
                overdue.append(task)
        if overdue:
            parts.append(f"\n⚠️ Просроченных: {len(overdue)}")

    # Слоны
    if projects and not is_weekend:
        parts.append("\n🐘 <b>Слоны:</b>")
        for p in projects[:3]:
            progress = project_progress.get(str(p.id), {})
            pct = progress.get("percent", 0)
            bar = _progress_bar(pct)
            parts.append(f"  {escape(p.title)} {bar} {pct}%")

    if not tasks and not frog and not projects:
        parts.append("\n🎉 Сегодня свободный день! Отдыхай или запланируй что-нибудь.")
    elif not tasks and not frog and projects:
        parts.append("\n📋 Задач на сегодня нет. Можно взять один маленький шаг по слону.")

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
