"""CRUD-операции для хронометража."""

import uuid
from datetime import datetime
from typing import List, Optional

import pendulum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import TimeTrackingEntry


async def create_time_entry(
    session: AsyncSession,
    user_id: int,
    activity_text: str,
    category: str,
    timestamp: Optional[datetime] = None,
    is_planned: bool = False,
    matched_task_id: Optional[uuid.UUID] = None,
    productivity_score: Optional[int] = None,
    bot_reaction: Optional[str] = None,
    duration_minutes: int = 15,
    commit: bool = True,
) -> TimeTrackingEntry:
    """Создать запись хронометража."""
    if timestamp is None:
        timestamp = pendulum.now("UTC")
    entry = TimeTrackingEntry(
        user_id=user_id,
        timestamp=timestamp,
        activity_text=activity_text,
        category=category,
        is_planned=is_planned,
        matched_task_id=matched_task_id,
        productivity_score=productivity_score,
        bot_reaction=bot_reaction,
        duration_minutes=duration_minutes,
    )
    session.add(entry)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(entry)
    return entry


async def get_today_entries(
    session: AsyncSession,
    user_id: int,
    tz: str = "Europe/Moscow",
) -> List[TimeTrackingEntry]:
    """Получить записи хронометража за сегодня."""
    now = pendulum.now(tz)
    start = now.start_of("day").in_tz("UTC")
    end = now.end_of("day").in_tz("UTC")
    result = await session.execute(
        select(TimeTrackingEntry)
        .where(
            TimeTrackingEntry.user_id == user_id,
            TimeTrackingEntry.timestamp >= start,
            TimeTrackingEntry.timestamp <= end,
        )
        .order_by(TimeTrackingEntry.timestamp.asc())
    )
    return list(result.scalars().all())


async def get_day_stats(
    session: AsyncSession,
    user_id: int,
    tz: str = "Europe/Moscow",
) -> dict:
    """Статистика дня по категориям."""
    entries = await get_today_entries(session, user_id, tz)
    stats = {"work": 0, "personal": 0, "rest": 0, "waste": 0, "focus": 0, "unknown": 0}
    total_minutes = 0
    total_productivity = 0
    scored_count = 0

    for e in entries:
        minutes = e.duration_minutes or 15
        stats[e.category] = stats.get(e.category, 0) + minutes
        total_minutes += minutes
        if e.productivity_score:
            total_productivity += e.productivity_score
            scored_count += 1

    return {
        "categories": stats,
        "total_minutes": total_minutes,
        "avg_productivity": round(total_productivity / scored_count, 1) if scored_count else 0,
        "entries_count": len(entries),
    }


async def get_week_stats(
    session: AsyncSession,
    user_id: int,
    tz: str = "Europe/Moscow",
    days_back: int = 7,
) -> dict:
    """Статистика за период (по умолчанию неделя)."""
    now = pendulum.now(tz)
    start = now.subtract(days=days_back).start_of("day").in_tz("UTC")
    result = await session.execute(
        select(TimeTrackingEntry)
        .where(
            TimeTrackingEntry.user_id == user_id,
            TimeTrackingEntry.timestamp >= start,
        )
        .order_by(TimeTrackingEntry.timestamp.asc())
    )
    entries = list(result.scalars().all())

    stats = {"work": 0, "personal": 0, "rest": 0, "waste": 0, "focus": 0, "unknown": 0}
    total_productivity = 0
    scored_count = 0

    for e in entries:
        minutes = e.duration_minutes or 15
        stats[e.category] = stats.get(e.category, 0) + minutes
        if e.productivity_score:
            total_productivity += e.productivity_score
            scored_count += 1

    return {
        "categories": stats,
        "avg_productivity": round(total_productivity / scored_count, 1) if scored_count else 0,
        "entries_count": len(entries),
    }
