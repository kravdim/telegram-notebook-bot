"""CRUD for persisted multi-step interaction state."""

from typing import Optional

import pendulum
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import InteractionState


async def get_state(session: AsyncSession, user_id: int) -> Optional[InteractionState]:
    """Return active interaction state, clearing it if expired."""
    result = await session.execute(
        select(InteractionState).where(InteractionState.user_id == user_id)
    )
    state = result.scalar_one_or_none()
    if not state:
        return None
    if state.expires_at and pendulum.instance(state.expires_at) < pendulum.now("UTC"):
        await clear_state(session, user_id)
        return None
    return state


async def set_state(
    session: AsyncSession,
    user_id: int,
    state_type: str,
    payload: Optional[dict] = None,
    ttl_minutes: int = 30,
) -> InteractionState:
    """Create or replace a user interaction state."""
    state = await get_state(session, user_id)
    expires_at = pendulum.now("UTC").add(minutes=ttl_minutes)
    if state:
        state.state_type = state_type
        state.payload = payload or {}
        state.expires_at = expires_at
        state.updated_at = pendulum.now("UTC")
    else:
        state = InteractionState(
            user_id=user_id,
            state_type=state_type,
            payload=payload or {},
            expires_at=expires_at,
        )
        session.add(state)
    await session.commit()
    await session.refresh(state)
    return state


async def clear_state(session: AsyncSession, user_id: int) -> None:
    """Clear any pending interaction state for a user."""
    await session.execute(delete(InteractionState).where(InteractionState.user_id == user_id))
    await session.commit()
