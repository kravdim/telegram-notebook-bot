"""Workflow ports independent of persistence and Telegram."""

from datetime import datetime
from typing import Literal, Protocol

WorkflowType = Literal[
    "complete_project", "memoir", "chronometry", "voice_confirm", "voice_processing", "voice_edit",
]


class InteractionRecord(Protocol):
    @property
    def state_type(self) -> str: ...

    @property
    def payload(self) -> dict: ...

    @property
    def expires_at(self) -> datetime | None: ...


class InteractionPort(Protocol):
    async def recover_interrupted(self) -> int: ...

    async def get(
        self, user_id: int, expected_type: WorkflowType | None = None,
    ) -> InteractionRecord | None: ...

    async def claim(
        self, user_id: int, state_type: WorkflowType, payload: dict | None = None,
        ttl_minutes: int = 30,
    ) -> InteractionRecord | None: ...

    async def transition(
        self, user_id: int, expected_type: WorkflowType, state_type: WorkflowType,
        payload: dict | None = None, ttl_minutes: int = 30, expected_token: str | None = None,
    ) -> InteractionRecord | None: ...

    async def clear(
        self, user_id: int, expected_type: WorkflowType, expected_token: str | None = None,
    ) -> bool: ...

    async def consume(
        self, user_id: int, expected_type: WorkflowType, expected_token: str | None = None,
    ) -> InteractionRecord | None: ...
