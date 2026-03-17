"""CRUD-операции для пользователей."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Получить пользователя по telegram_id."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str,
    role: str = "user",
    timezone: str = "Europe/Moscow",
) -> tuple[User, bool]:
    """Получить или создать пользователя. Возвращает (user, created)."""
    user = await get_user(session, telegram_id)
    if user:
        return user, False

    user = User(
        telegram_id=telegram_id,
        username=username,
        role=role,
        timezone=timezone,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, True


async def update_user_settings(
    session: AsyncSession,
    telegram_id: int,
    **kwargs,
) -> User | None:
    """Обновить настройки пользователя."""
    user = await get_user(session, telegram_id)
    if not user:
        return None

    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)

    await session.commit()
    await session.refresh(user)
    return user


async def get_all_users(session: AsyncSession) -> list[User]:
    """Получить всех пользователей (для admin)."""
    result = await session.execute(select(User))
    return list(result.scalars().all())
