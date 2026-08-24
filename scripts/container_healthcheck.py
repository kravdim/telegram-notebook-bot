#!/usr/bin/env python3
"""Container readiness probe: event loop heartbeat, config, DB and migrations."""

import asyncio
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.config import BASE_DIR, settings
    from bot.db.engine import engine
    from bot.runtime.readiness import validate_readiness_file

    errors = settings.runtime_config_errors()
    if errors:
        raise SystemExit("Invalid runtime configuration: " + "; ".join(errors))

    readiness_file = os.environ.get("READINESS_FILE", "/tmp/dailyplanner-ready.json")
    max_age = float(os.environ.get("READINESS_MAX_AGE_SECONDS", "20"))
    try:
        heartbeat = validate_readiness_file(readiness_file, max_age)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"Runtime is not ready: {exc}") from exc

    alembic_cfg = Config(str(BASE_DIR / "alembic.ini"))
    expected = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    try:
        async with engine.connect() as connection:
            current = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    if not expected or current != expected:
        raise SystemExit(f"Migration mismatch: database={current!r}, code={expected!r}")
    print(f"container ready: pid={heartbeat['pid']}, migration={current}")


if __name__ == "__main__":
    asyncio.run(main())
