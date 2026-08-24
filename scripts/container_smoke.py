#!/usr/bin/env python3
"""Long-running disposable application probe used by container E2E."""

import argparse
import asyncio
import os
import signal
import sys
import uuid
from pathlib import Path

from sqlalchemy import delete, select, text


async def exercise_schema() -> None:
    """Exercise migrated schema and pgvector without retaining test data."""
    from bot.db.engine import async_session
    from bot.db.models import Task, User

    user_id = 8_700_000_000 + int(uuid.uuid4().hex[:6], 16)
    task_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    vector = "[" + ",".join(["0"] * 768) + "]"
    async with async_session() as session:
        extensions = set(
            (
                await session.execute(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('vector', 'pg_trgm', 'pgcrypto')"
                    )
                )
            ).scalars()
        )
        if extensions != {"vector", "pg_trgm", "pgcrypto"}:
            raise RuntimeError(f"required PostgreSQL extensions missing: {extensions}")
        session.add(User(telegram_id=user_id, username="container-smoke"))
        await session.flush()
        session.add(Task(id=task_id, user_id=user_id, title="smoke task"))
        await session.commit()
        title = await session.scalar(select(Task.title).where(Task.id == task_id))
        await session.execute(
            text(
                "INSERT INTO knowledge_base (id, source, topic, content, embedding) "
                "VALUES (:id, 'container-smoke', 'readiness', 'vector roundtrip', "
                "CAST(:value AS vector))"
            ),
            {"id": chunk_id, "value": vector},
        )
        dimensions = await session.scalar(
            text("SELECT vector_dims(embedding) FROM knowledge_base WHERE id = :id"),
            {"id": chunk_id},
        )
        if title != "smoke task" or dimensions != 768:
            raise RuntimeError("schema/vector roundtrip returned an unexpected result")
        await session.execute(delete(User).where(User.telegram_id == user_id))
        await session.execute(
            text("DELETE FROM knowledge_base WHERE id = :id"), {"id": chunk_id}
        )
        await session.commit()


async def main(once: bool = False) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.config import settings
    from bot.db.engine import engine
    from bot.runtime.readiness import RuntimeReadiness
    from bot.runtime.singleton import SingletonLease

    errors = settings.runtime_config_errors()
    if errors:
        raise SystemExit("Invalid runtime configuration: " + "; ".join(errors))

    singleton = SingletonLease(engine)
    if not await singleton.acquire():
        raise SystemExit("container smoke could not acquire the runtime singleton")
    readiness = RuntimeReadiness(
        os.environ.get("READINESS_FILE", "/tmp/dailyplanner-ready.json"),
        interval_seconds=2,
    )
    try:
        await exercise_schema()
        if once:
            print("container smoke ok")
            return
        await readiness.start()
        print("container smoke ready")
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stopped.set)
        await stopped.wait()
    finally:
        await readiness.stop()
        await singleton.release()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
