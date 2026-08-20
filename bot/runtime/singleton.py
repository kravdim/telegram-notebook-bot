"""PostgreSQL-backed singleton lease for the bot process."""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)


class SingletonLease:
    """Удерживает session-level advisory lock на отдельном DB-соединении."""

    def __init__(self, engine: AsyncEngine, name: str = "dailyplanner:runtime") -> None:
        self._engine = engine
        self._name = name
        self._connection: AsyncConnection | None = None

    @property
    def acquired(self) -> bool:
        return self._connection is not None

    async def acquire(self) -> bool:
        if self._connection is not None:
            return True
        connection = await self._engine.connect()
        result = await connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"),
            {"name": self._name},
        )
        if not bool(result.scalar_one()):
            await connection.close()
            logger.error("Singleton lease уже занят: %s", self._name)
            return False
        self._connection = connection
        logger.info("Singleton lease получен: %s", self._name)
        return True

    async def release(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:name))"),
                {"name": self._name},
            )
        finally:
            await connection.close()
        logger.info("Singleton lease освобождён: %s", self._name)

    async def __aenter__(self) -> "SingletonLease":
        if not await self.acquire():
            raise RuntimeError(f"singleton lease is already held: {self._name}")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()
