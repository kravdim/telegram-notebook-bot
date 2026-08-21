#!/usr/bin/env python3
"""Run migrated integration, backup and restore checks on throw-away databases."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


def _run(command: list[str], env: dict[str, str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if result.stdout else ""


def _pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("/opt/homebrew/opt").glob(f"postgresql*/bin/{name}"), reverse=True)
    if candidates:
        return str(candidates[0])
    raise SystemExit(f"required PostgreSQL client tool is missing: {name}")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    from bot.config import settings

    source_url = make_url(settings.database_url)
    if not source_url.drivername.startswith("postgresql"):
        raise SystemExit("PostgreSQL is required")
    database = f"dailyplanner_ci_drill_{secrets.token_hex(5)}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project)
    env["PGPASSWORD"] = source_url.password or ""
    cli = ["-h", source_url.host or "localhost", "-p", str(source_url.port or 5432)]
    if source_url.username:
        cli += ["-U", source_url.username]
    drill_url = source_url.set(database=database).render_as_string(hide_password=False)
    env["DATABASE_URL"] = drill_url
    env["RUN_DB_TESTS"] = "1"
    env.setdefault("ALLOW_ALL_USERS", "true")
    created = False
    try:
        _run([_pg_tool("createdb"), *cli, database], env)
        created = True
        _run([str(project / ".venv/bin/alembic"), "upgrade", "head"], env)
        _run(
            [
                str(project / ".venv/bin/pytest"),
                "-q",
                "tests/integration/test_restart_and_concurrency.py",
            ],
            env,
        )
        with tempfile.TemporaryDirectory(prefix="dailyplanner-backup-drill-") as backup_dir:
            env["BACKUP_DIR"] = backup_dir
            backup_output = _run(
                [
                    str(project / ".venv/bin/python"),
                    "-c",
                    "import asyncio; from bot.scheduler.backup import run_backup; "
                    "print(asyncio.run(run_backup()))",
                ],
                env,
                capture=True,
            )
            backup = Path(backup_output.splitlines()[-1])
            _run(
                [
                    str(project / ".venv/bin/python"),
                    "scripts/restore_drill.py",
                    str(backup),
                    "--database-url",
                    drill_url,
                ],
                env,
            )
    finally:
        if created:
            _run([_pg_tool("dropdb"), *cli, "--force", database], env)


if __name__ == "__main__":
    main()
