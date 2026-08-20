#!/usr/bin/env python3
"""Fail-fast readiness checks shared by every deployment target."""

import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.config import BASE_DIR, settings
    from bot.db.engine import engine

    errors = settings.runtime_config_errors()
    if errors:
        raise SystemExit("Invalid runtime configuration: " + "; ".join(errors))

    alembic_cfg = Config(str(BASE_DIR / "alembic.ini"))
    expected = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    async with engine.connect() as connection:
        current = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        await connection.execute(text("SELECT 1"))
    await engine.dispose()

    if not expected or current != expected:
        raise SystemExit(f"Migration mismatch: database={current!r}, code={expected!r}")
    print(f"preflight ok: database reachable, migration={current}")


if __name__ == "__main__":
    asyncio.run(main())
