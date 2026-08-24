"""Auditable deletion of all data directly associated with a Telegram user."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Base, DeliveryBatch, DeliveryPart, FsmState, LlmLog, User


def confirmation_phrase(user_id: int) -> str:
    return f"DELETE-{user_id}"


async def user_data_counts(session: AsyncSession, user_id: int) -> dict[str, int]:
    """Return content-free row counts for every user-owned table."""
    counts: dict[str, int] = {}
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        if "user_id" not in table.c:
            continue
        result = await session.execute(
            select(func.count()).select_from(table).where(table.c.user_id == user_id)
        )
        counts[table.name] = result.scalar_one()

    user_result = await session.execute(
        select(func.count()).select_from(User).where(User.telegram_id == user_id)
    )
    counts[User.__tablename__] = user_result.scalar_one()
    fsm_result = await session.execute(
        select(func.count())
        .select_from(FsmState)
        .where(func.split_part(FsmState.storage_key, ":", 3) == str(user_id))
    )
    counts[FsmState.__tablename__] = fsm_result.scalar_one()
    delivery_parts = await session.execute(
        select(func.count())
        .select_from(DeliveryPart)
        .join(DeliveryBatch, DeliveryPart.batch_id == DeliveryBatch.id)
        .where(DeliveryBatch.user_id == user_id)
    )
    counts[DeliveryPart.__tablename__] = delivery_parts.scalar_one()
    return counts


async def delete_user_data(session: AsyncSession, user_id: int) -> dict[str, int]:
    """Delete user data in the current transaction and verify no rows remain."""
    before = await user_data_counts(session, user_id)
    await session.execute(delete(LlmLog).where(LlmLog.user_id == user_id))
    await session.execute(
        delete(FsmState).where(func.split_part(FsmState.storage_key, ":", 3) == str(user_id))
    )
    await session.execute(delete(User).where(User.telegram_id == user_id))
    await session.flush()

    remaining = {
        table: count for table, count in (await user_data_counts(session, user_id)).items() if count
    }
    if remaining:
        raise RuntimeError(f"user data verification failed: {remaining}")
    return before
