"""Typed task-creation use case independent of LLM and Telegram transports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

import pendulum


@dataclass(frozen=True)
class CreateTaskDependencies:
    session_factory: Callable[[], Any]
    get_active_trip: Callable[..., Awaitable[Any]]
    search_tasks: Callable[..., Awaitable[list[Any]]]
    normalize_identity: Callable[[str], str]
    valid_repeat_rule: Callable[[str | None], bool]
    update_task: Callable[..., Awaitable[Any]]
    set_frog: Callable[..., Awaitable[Any]]
    upsert_task_reminder: Callable[..., Awaitable[Any]]
    get_frog: Callable[..., Awaitable[Any]]
    create_task: Callable[..., Awaitable[Any]]
    create_reminder: Callable[..., Awaitable[Any]]
    count_similar_completed: Callable[..., Awaitable[tuple[int, Any]]]
    sanitize_title: Callable[[str], str]
    validate_title: Callable[[str], str | None]
    parse_date: Callable[[str | None, str], date | None]
    parse_time: Callable[[str | None], time | None]
    parse_datetime: Callable[[str | None, str], datetime | None]
    format_repeat_rule: Callable[[str], str]
    recurring_comment: Callable[[str, int, Any, str], str]


@dataclass(frozen=True)
class _PreparedTask:
    title: str
    category: str
    priority: str
    is_frog: bool
    scheduled_date: date | None
    due_date: date | None
    due_time: time | None
    remind_at: datetime | None
    remind_before_min: int | None
    repeat_rule: str | None


@dataclass(frozen=True)
class _DuplicateTask:
    id: Any
    title: str
    scheduled_date: date | None
    due_date: date | None
    due_time: time | None
    is_frog: bool
    priority: str
    repeat_rule: str | None


def _prepare_task(
    args: Mapping[str, Any], timezone: str, dependencies: CreateTaskDependencies
) -> tuple[_PreparedTask | None, str | None]:
    title = dependencies.sanitize_title(str(args.get("title", "")))
    if error := dependencies.validate_title(title):
        return None, error

    scheduled_date = dependencies.parse_date(args.get("scheduled_date"), timezone)
    due_date = dependencies.parse_date(args.get("due_date"), timezone)
    due_time = dependencies.parse_time(args.get("due_time"))
    remind_at = dependencies.parse_datetime(args.get("remind_at"), timezone)
    if args.get("scheduled_date") and scheduled_date is None:
        return None, "Не удалось распознать дату планирования. Уточни дату."
    if args.get("due_date") and due_date is None:
        return None, "Не удалось распознать дедлайн. Уточни дату."
    if args.get("due_time") and due_time is None:
        return None, "Не удалось распознать время. Укажи его в формате ЧЧ:ММ."
    if args.get("remind_at") and remind_at is None:
        return None, "Не удалось распознать время напоминания. Уточни дату и время."

    today = pendulum.now(timezone).date()
    if scheduled_date and scheduled_date < today:
        return None, "Дата планирования в прошлом. Уточни дату."
    if due_date and due_date < today:
        return None, "Дата дедлайна в прошлом. Уточни дату."

    repeat_rule = args.get("repeat_rule")
    if repeat_rule is not None:
        repeat_rule = str(repeat_rule)
    if not dependencies.valid_repeat_rule(repeat_rule):
        return None, "Не удалось распознать правило повторения. Уточни периодичность."
    if repeat_rule and not scheduled_date and not due_date:
        scheduled_date = today

    category = str(args.get("category", "work"))
    if category not in ("work", "personal"):
        category = "work"
    priority = str(args.get("priority", "normal"))
    if priority not in ("high", "medium", "normal"):
        priority = "normal"
    remind_before = args.get("remind_before_min")
    return _PreparedTask(
        title=title,
        category=category,
        priority=priority,
        is_frog=bool(args.get("is_frog", False)),
        scheduled_date=scheduled_date,
        due_date=due_date,
        due_time=due_time,
        remind_at=remind_at,
        remind_before_min=int(remind_before) if remind_before is not None else None,
        repeat_rule=repeat_rule,
    ), None


async def _load_context(
    user_id: int,
    task: _PreparedTask,
    timezone: str,
    dependencies: CreateTaskDependencies,
) -> tuple[Any | None, _DuplicateTask | None]:
    trip_id = None
    async with dependencies.session_factory() as session:
        trip = await dependencies.get_active_trip(
            session, user_id, pendulum.now(timezone).date()
        )
        if trip:
            trip_id = trip.id

    duplicate = None
    async with dependencies.session_factory() as session:
        matches = await dependencies.search_tasks(
            session, user_id, task.title, status="open"
        )
        normalized = dependencies.normalize_identity(task.title)
        existing = next(
            (
                candidate
                for candidate in matches
                if dependencies.normalize_identity(candidate.title) == normalized
            ),
            None,
        )
        if existing:
            duplicate = _DuplicateTask(
                id=existing.id,
                title=existing.title,
                scheduled_date=existing.scheduled_date,
                due_date=existing.due_date,
                due_time=existing.due_time,
                is_frog=existing.is_frog,
                priority=existing.priority,
                repeat_rule=existing.repeat_rule,
            )
    return trip_id, duplicate


def _duplicate_updates(task: _PreparedTask, duplicate: _DuplicateTask) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for field in ("scheduled_date", "due_date", "due_time"):
        value = getattr(task, field)
        if value and value != getattr(duplicate, field):
            updates[field] = value
    if task.remind_at:
        updates["remind_at"] = task.remind_at
    if task.is_frog and not duplicate.is_frog:
        updates["is_frog"] = True
    if task.priority != "normal" and task.priority != duplicate.priority:
        updates["priority"] = task.priority
    if task.repeat_rule and task.repeat_rule != duplicate.repeat_rule:
        updates["repeat_rule"] = task.repeat_rule
    return updates


def _format_duplicate_changes(
    updates: Mapping[str, Any], task: _PreparedTask, frog_changed: bool
) -> str:
    changes = []
    if "scheduled_date" in updates and task.scheduled_date:
        changes.append(f"📅 {task.scheduled_date.strftime('%d.%m.%Y')}")
    if "due_date" in updates and task.due_date:
        changes.append(f"⏳ до {task.due_date.strftime('%d.%m.%Y')}")
    if "due_time" in updates and task.due_time:
        changes.append(f"⏰ {task.due_time.strftime('%H:%M')}")
    if task.remind_at:
        changes.append(f"🔔 {task.remind_at.strftime('%d.%m %H:%M')}")
    if "priority" in updates:
        changes.append(f"приоритет {task.priority}")
    if "repeat_rule" in updates and task.repeat_rule:
        changes.append(f"🔄 {task.repeat_rule}")
    if frog_changed:
        changes.append("🐸 лягушка")
    return " ".join(changes)


async def _update_duplicate(
    user_id: int,
    task: _PreparedTask,
    duplicate: _DuplicateTask,
    dependencies: CreateTaskDependencies,
) -> str:
    updates = _duplicate_updates(task, duplicate)
    if not updates:
        return f"Задача «{duplicate.title}» уже существует ✅"

    persisted_updates = dict(updates)
    frog_changed = bool(persisted_updates.pop("is_frog", False))
    async with dependencies.session_factory() as session:
        if frog_changed:
            await dependencies.set_frog(session, duplicate.id, user_id, commit=False)
        if persisted_updates:
            await dependencies.update_task(
                session, duplicate.id, user_id, commit=False, **persisted_updates
            )
        if task.remind_at:
            await dependencies.upsert_task_reminder(
                session,
                user_id,
                duplicate.id,
                duplicate.title,
                task.remind_at,
                None,
                commit=False,
            )
        await session.commit()
    changes = _format_duplicate_changes(updates, task, frog_changed)
    return f"Задача «{duplicate.title}» уже есть — обновил: {changes} ✅"


async def _create_new_task(
    user_id: int,
    task: _PreparedTask,
    trip_id: Any | None,
    dependencies: CreateTaskDependencies,
) -> tuple[Any, int, Any]:
    async with dependencies.session_factory() as session:
        if task.is_frog:
            current_frog = await dependencies.get_frog(session, user_id)
            if current_frog:
                current_frog.is_frog = False
        created = await dependencies.create_task(
            session,
            user_id=user_id,
            title=task.title,
            category=task.category,
            priority=task.priority,
            is_frog=task.is_frog,
            scheduled_date=task.scheduled_date,
            due_date=task.due_date,
            due_time=task.due_time,
            remind_at=task.remind_at,
            remind_before_min=task.remind_before_min,
            repeat_rule=task.repeat_rule,
            trip_id=trip_id,
            commit=False,
        )
        if task.remind_at:
            await dependencies.create_reminder(
                session,
                user_id,
                message=task.title,
                remind_at=task.remind_at,
                repeat_rule=None,
                task_id=created.id,
                commit=False,
            )
        await session.commit()
        await session.refresh(created)
        count, last_at = await dependencies.count_similar_completed(
            session, user_id, task.title
        )
    return created, count, last_at


def _format_created_task(
    created: Any,
    task: _PreparedTask,
    similar_count: int,
    last_at: Any,
    timezone: str,
    dependencies: CreateTaskDependencies,
) -> str:
    parts = [f"Задача создана: {created.title}"]
    if task.scheduled_date:
        parts.append(f"📅 {task.scheduled_date.strftime('%d.%m.%Y')}")
    if task.due_date:
        parts.append(f"⏳ до {task.due_date.strftime('%d.%m.%Y')}")
    if task.due_time:
        parts.append(f"⏰ {task.due_time.strftime('%H:%M')}")
    if task.remind_at:
        parts.append(f"🔔 Напомню: {task.remind_at.strftime('%d.%m %H:%M')}")
    if task.is_frog:
        parts.append("🐸 Лягушка!")
    if task.repeat_rule:
        parts.append(f"🔄 {dependencies.format_repeat_rule(task.repeat_rule)}")
    result = " ".join(parts) + " ✅"
    if similar_count >= 2:
        result += "\n" + dependencies.recurring_comment(
            task.title, similar_count, last_at, timezone
        )
    return result


async def execute_create_task(
    user_id: int,
    args: Mapping[str, Any],
    timezone: str,
    dependencies: CreateTaskDependencies,
) -> str:
    """Validate and atomically create or enrich one task."""
    task, error = _prepare_task(args, timezone, dependencies)
    if task is None:
        return error or "Не удалось создать задачу."
    trip_id, duplicate = await _load_context(user_id, task, timezone, dependencies)
    if duplicate is not None:
        return await _update_duplicate(user_id, task, duplicate, dependencies)
    created, similar_count, last_at = await _create_new_task(
        user_id, task, trip_id, dependencies
    )
    return _format_created_task(
        created, task, similar_count, last_at, timezone, dependencies
    )
