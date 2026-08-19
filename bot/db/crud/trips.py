"""CRUD-операции для командировок."""

import uuid
from datetime import date
from typing import List, Optional

import pendulum
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Trip


async def create_trip(
    session: AsyncSession,
    user_id: int,
    title: str,
    start_date: date,
    end_date: date,
    destination: Optional[str] = None,
    timezone: Optional[str] = None,
) -> Trip:
    """Создать командировку."""
    trip = Trip(
        user_id=user_id,
        title=title,
        start_date=start_date,
        end_date=end_date,
        destination=destination,
        timezone=timezone,
    )
    session.add(trip)
    await session.commit()
    await session.refresh(trip)
    return trip


async def get_active_trip(
    session: AsyncSession,
    user_id: int,
    today: Optional[date] = None,
) -> Optional[Trip]:
    """Получить текущую активную командировку.

    Старые записи со статусом active, у которых уже прошёл end_date, не считаются
    активными для дайджестов и команды /trip.
    """
    today = today or pendulum.today("UTC").date()
    result = await session.execute(
        select(Trip)
        .where(
            Trip.user_id == user_id,
            Trip.status == "active",
            Trip.start_date <= today,
            Trip.end_date >= today,
        )
        .order_by(Trip.start_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_open_trip(
    session: AsyncSession,
    user_id: int,
    today: Optional[date] = None,
) -> Optional[Trip]:
    """Получить незавершённую поездку, завершив просроченные записи."""
    today = today or pendulum.today("UTC").date()
    expired_result = await session.execute(
        select(Trip).where(
            Trip.user_id == user_id,
            Trip.status == "active",
            Trip.end_date < today,
        )
    )
    expired = list(expired_result.scalars().all())
    for trip in expired:
        trip.status = "completed"
    if expired:
        await session.commit()
    result = await session.execute(
        select(Trip)
        .where(Trip.user_id == user_id, Trip.status == "active")
        .order_by(Trip.start_date.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def complete_trip(
    session: AsyncSession,
    trip_id: uuid.UUID,
    user_id: int,
) -> Optional[Trip]:
    """Завершить командировку."""
    result = await session.execute(
        select(Trip).where(Trip.id == trip_id, Trip.user_id == user_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        return None
    trip.status = "completed"
    await session.commit()
    await session.refresh(trip)
    return trip


async def get_user_trips(
    session: AsyncSession,
    user_id: int,
) -> List[Trip]:
    """Получить все командировки пользователя."""
    result = await session.execute(
        select(Trip)
        .where(Trip.user_id == user_id)
        .order_by(Trip.start_date.desc())
    )
    return list(result.scalars().all())
