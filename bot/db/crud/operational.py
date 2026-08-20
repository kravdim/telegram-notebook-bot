"""CRUD for persistent operational state and SLO markers."""

from typing import Optional

import pendulum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OperationalState


async def get_operational_state(
    session: AsyncSession, key: str
) -> Optional[OperationalState]:
    result = await session.execute(
        select(OperationalState).where(OperationalState.key == key)
    )
    return result.scalar_one_or_none()


async def set_operational_state(
    session: AsyncSession, key: str, value: dict
) -> OperationalState:
    row = await get_operational_state(session, key)
    if row:
        row.value = value
        row.updated_at = pendulum.now("UTC")
    else:
        row = OperationalState(key=key, value=value)
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
