"""Maintenance ownership of the bot's advisory lock and connection drain."""

import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class MaintenanceLease:
    """Dedicated connection; never interpret a cached acquired flag as proof."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.connection: AsyncConnection | None = None
        self.pid: int | None = None

    async def acquire(self, timeout: float = 30) -> None:
        if self.connection is not None:
            await self.assert_exclusive()
            return
        connection = await self.engine.connect()
        try:
            deadline = time.monotonic() + timeout
            while True:
                acquired = await connection.scalar(text(
                    "SELECT pg_try_advisory_lock(hashtext('dailyplanner:runtime'))"
                ))
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Bot writer lease did not drain before timeout")
                await asyncio.sleep(0.2)
            self.pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            self.connection = connection
            while True:
                try:
                    await self.assert_exclusive()
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.2)
        except BaseException:
            if self.connection is not None:
                await self.release()
            else:
                await connection.close()
            raise

    async def assert_exclusive(self) -> None:
        connection = self.connection
        if connection is None or connection.closed or connection.invalidated:
            raise RuntimeError("Maintenance writer lease is not held")
        # pg_stat_activity can otherwise be cached for this long-lived transaction.
        await connection.execute(text("SELECT pg_stat_clear_snapshot()"))
        row = (await connection.execute(text(
            "SELECT pg_backend_pid(), "
            "EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND granted AND objsubid=1 "
            "AND objid=(hashtext('dailyplanner:runtime')::bigint & 4294967295)::oid "
            "AND classid=(CASE WHEN hashtext('dailyplanner:runtime') < 0 "
            "THEN 4294967295 ELSE 0 END)::oid), "
            "EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid != pg_backend_pid()), "
            "EXISTS (SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())"
        ))).one()
        if row[0] != self.pid or not row[1] or row[2] or row[3]:
            raise RuntimeError("Maintenance requires live lease and no other database clients")

    async def release(self) -> None:
        connection, self.connection = self.connection, None
        self.pid = None
        if connection is None:
            return
        try:
            if not connection.closed and not connection.invalidated:
                await connection.execute(text(
                    "SELECT pg_advisory_unlock(hashtext('dailyplanner:runtime'))"
                ))
        finally:
            await connection.close()
