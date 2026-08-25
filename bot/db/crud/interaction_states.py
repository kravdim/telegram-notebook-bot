"""CRUD for persisted multi-step interaction state."""

from typing import Optional

import pendulum
from sqlalchemy import delete, func, select
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
        await session.delete(state)
        await session.commit()
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


async def claim_state(
    session: AsyncSession,
    user_id: int,
    state_type: str,
    payload: Optional[dict] = None,
    ttl_minutes: int = 30,
) -> Optional[InteractionState]:
    """Create a state only when no other active workflow owns the user slot."""
    await session.execute(select(func.pg_advisory_xact_lock(user_id)))
    result = await session.execute(
        select(InteractionState)
        .where(InteractionState.user_id == user_id)
        .with_for_update()
    )
    state = result.scalar_one_or_none()
    now = pendulum.now("UTC")
    if state and (not state.expires_at or pendulum.instance(state.expires_at) >= now):
        return None
    if state:
        await session.delete(state)
        await session.flush()
    state = InteractionState(
        user_id=user_id,
        state_type=state_type,
        payload=payload or {},
        expires_at=now.add(minutes=ttl_minutes),
    )
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state


async def transition_state(
    session: AsyncSession,
    user_id: int,
    expected_type: str,
    state_type: str,
    payload: Optional[dict] = None,
    ttl_minutes: int = 30,
) -> Optional[InteractionState]:
    """Compare-and-set an active workflow without replacing another type."""
    result = await session.execute(
        select(InteractionState)
        .where(InteractionState.user_id == user_id)
        .with_for_update()
    )
    state = result.scalar_one_or_none()
    now = pendulum.now("UTC")
    if (
        not state
        or state.state_type != expected_type
        or (state.expires_at and pendulum.instance(state.expires_at) < now)
    ):
        return None
    state.state_type = state_type
    state.payload = payload or {}
    state.expires_at = now.add(minutes=ttl_minutes)
    state.updated_at = now
    await session.commit()
    await session.refresh(state)
    return state


async def clear_state(session: AsyncSession, user_id: int) -> None:
    """Clear any pending interaction state for a user."""
    await session.execute(delete(InteractionState).where(InteractionState.user_id == user_id))
    await session.commit()


async def clear_state_if_type(
    session: AsyncSession, user_id: int, expected_type: str
) -> bool:
    """Clear a workflow only if the caller still owns its state type."""
    result = await session.execute(
        delete(InteractionState)
        .where(
            InteractionState.user_id == user_id,
            InteractionState.state_type == expected_type,
        )
        .returning(InteractionState.user_id)
    )
    cleared = result.scalar_one_or_none() is not None
    await session.commit()
    return cleared


async def consume_state(
    session: AsyncSession, user_id: int, expected_type: str
) -> Optional[InteractionState]:
    """Atomically return and clear the workflow only while its type still matches."""
    result = await session.execute(
        select(InteractionState)
        .where(InteractionState.user_id == user_id)
        .with_for_update()
    )
    state = result.scalar_one_or_none()
    now = pendulum.now("UTC")
    if not state or state.state_type != expected_type:
        return None
    if state.expires_at and pendulum.instance(state.expires_at) < now:
        await session.delete(state)
        await session.commit()
        return None
    await session.delete(state)
    await session.commit()
    return state
