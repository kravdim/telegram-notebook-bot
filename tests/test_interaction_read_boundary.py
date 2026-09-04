from types import SimpleNamespace
from unittest.mock import AsyncMock

import pendulum
import pytest

from bot.db.crud.interaction_states import get_state


@pytest.mark.asyncio
async def test_expired_read_does_not_delete_or_commit():
    old = SimpleNamespace(expires_at=pendulum.now("UTC").subtract(minutes=1))
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: old)),
        delete=AsyncMock(),
        commit=AsyncMock(),
    )
    assert await get_state(session, 42) is None
    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()
