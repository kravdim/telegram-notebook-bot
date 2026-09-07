"""PostgreSQL-backed aiogram FSM storage."""

from collections.abc import Mapping
from typing import Any

import pendulum
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

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

    async def _update(self, key: StorageKey, values: dict, *, merge: bool = False) -> dict:
        """Атомарно менять только переданные поля, включая JSONB merge."""
        statement = insert(FsmState).values(
            storage_key=_serialize_key(key), **values, updated_at=pendulum.now("UTC")
        )
        changes = {**values, "updated_at": pendulum.now("UTC")}
        if merge:
            changes["data"] = FsmState.data.op("||")(statement.excluded.data)
        returning = statement.on_conflict_do_update(
            index_elements=[FsmState.storage_key], set_=changes
        ).returning(FsmState.data)
        async with async_session() as session:
            data = (await session.execute(returning)).scalar_one()
            await session.commit()
            return dict(data or {})

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._update(key, {"state": value})

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await self._get(key)
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._update(key, {"data": dict(data)})

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        return await self._update(key, {"data": dict(data)}, merge=True)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, data = await self._get(key)
        return data.copy()

    async def close(self) -> None:
        return None
