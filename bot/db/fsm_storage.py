"""PostgreSQL-backed aiogram FSM storage."""

from collections.abc import Mapping
from typing import Any

import pendulum
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import select

from bot.db.engine import async_session
from bot.db.models import FsmState


def _serialize_key(key: StorageKey) -> str:
    return ":".join(
        str(value or "")
        for value in (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id,
            key.business_connection_id,
            key.destiny,
        )
    )


class DatabaseFSMStorage(BaseStorage):
    """Хранит onboarding и другие FSM-сценарии между рестартами."""

    async def _get(self, key: StorageKey):
        async with async_session() as session:
            result = await session.execute(
                select(FsmState).where(FsmState.storage_key == _serialize_key(key))
            )
            row = result.scalar_one_or_none()
            if not row:
                return None, {}
            return row.state, dict(row.data or {})

    async def _write(self, key: StorageKey, state: str | None, data: dict) -> None:
        storage_key = _serialize_key(key)
        async with async_session() as session:
            result = await session.execute(
                select(FsmState).where(FsmState.storage_key == storage_key)
            )
            row = result.scalar_one_or_none()
            if state is None and not data:
                if row:
                    await session.delete(row)
                    await session.commit()
                return
            if row:
                row.state = state
                row.data = data
                row.updated_at = pendulum.now("UTC")
            else:
                session.add(FsmState(storage_key=storage_key, state=state, data=data))
            await session.commit()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        _, data = await self._get(key)
        value = state.state if isinstance(state, State) else state
        await self._write(key, value, data)

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await self._get(key)
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        state, _ = await self._get(key)
        await self._write(key, state, dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, data = await self._get(key)
        return data.copy()

    async def close(self) -> None:
        return None
