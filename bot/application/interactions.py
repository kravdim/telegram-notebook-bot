"""Application service owning persisted multi-step workflow transitions."""

from typing import Literal

from bot.db.crud.interaction_states import (
    claim_state,
    clear_state_if_type,
    consume_state,
    get_state,
    transition_state,
)
from bot.db.engine import async_session
from bot.db.models import InteractionState

WorkflowType = Literal[
    "complete_project", "memoir", "chronometry", "voice_confirm", "voice_edit"
]


class InteractionService:
    async def get(
        self, user_id: int, expected_type: WorkflowType | None = None
    ) -> InteractionState | None:
        async with async_session() as session:
            state = await get_state(session, user_id)
            if expected_type is not None and state and state.state_type != expected_type:
                return None
            return state

    async def claim(
        self,
        user_id: int,
        state_type: WorkflowType,
        payload: dict | None = None,
        ttl_minutes: int = 30,
    ) -> InteractionState | None:
        async with async_session() as session:
            return await claim_state(
                session, user_id, state_type, payload, ttl_minutes
            )

    async def transition(
        self,
        user_id: int,
        expected_type: WorkflowType,
        state_type: WorkflowType,
        payload: dict | None = None,
        ttl_minutes: int = 30,
    ) -> InteractionState | None:
        async with async_session() as session:
            return await transition_state(
                session,
                user_id,
                expected_type,
                state_type,
                payload,
                ttl_minutes,
            )

    async def clear(self, user_id: int, expected_type: WorkflowType) -> bool:
        async with async_session() as session:
            return await clear_state_if_type(session, user_id, expected_type)

    async def consume(
        self, user_id: int, expected_type: WorkflowType
    ) -> InteractionState | None:
        async with async_session() as session:
            return await consume_state(session, user_id, expected_type)


interaction_service = InteractionService()
