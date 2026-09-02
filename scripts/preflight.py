#!/usr/bin/env python3
"""Fail-fast readiness checks shared by every deployment target."""

import argparse
import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-pending-migration",
        action="store_true",
        help="accept a deployed revision that is an ancestor of the candidate head",
    )
    parser.add_argument(
        "--compatible-database-head",
        help=(
            "accept this exact newer database head during an explicitly "
            "declared backward-compatible code rollback"
        ),
    )
    return parser.parse_args()


async def main(
    *,
    allow_pending_migration: bool = False,
    compatible_database_head: str | None = None,
) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.config import BASE_DIR, settings
    from bot.db.engine import engine

    errors = settings.runtime_config_errors()
    if errors:
        raise SystemExit("Invalid runtime configuration: " + "; ".join(errors))

    alembic_cfg = Config(str(BASE_DIR / "alembic.ini"))
    migrations = ScriptDirectory.from_config(alembic_cfg)
    expected = migrations.get_current_head()
    async with engine.connect() as connection:
        current = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        await connection.execute(text("SELECT 1"))
    await engine.dispose()

    migration_matches = current == expected
    migration_is_declared_compatible = bool(
        compatible_database_head and current == compatible_database_head
    )
    migration_can_upgrade = False
    if allow_pending_migration and not migration_matches:
        known_revisions = {revision.revision for revision in migrations.walk_revisions()}
        migration_can_upgrade = current in known_revisions
    if not expected or not (
        migration_matches or migration_can_upgrade or migration_is_declared_compatible
    ):
        raise SystemExit(f"Migration mismatch: database={current!r}, code={expected!r}")
    if migration_matches:
        state = "current"
    elif migration_can_upgrade:
        state = "pending-compatible"
    else:
        state = "newer-compatible"
    print(f"preflight ok: database reachable, migration={current}, state={state}")


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            allow_pending_migration=args.allow_pending_migration,
            compatible_database_head=args.compatible_database_head,
        )
    )
