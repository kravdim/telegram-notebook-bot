#!/usr/bin/env python3
"""Run migrated integration, backup and restore checks on throw-away databases."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
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
    operator_url_raw = os.environ.get("OPERATOR_DATABASE_URL")
    if not operator_url_raw:
        raise SystemExit("OPERATOR_DATABASE_URL is required")
    operator_url = make_url(operator_url_raw)
    if not operator_url.drivername.startswith("postgresql"):
        raise SystemExit("PostgreSQL operator URL is required")
    database = f"dailyplanner_ci_drill_{secrets.token_hex(5)}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project)
    env["PGPASSWORD"] = operator_url.password or ""
    cli = [
        "-h",
        operator_url.host or "localhost",
        "-p",
        str(operator_url.port or 5432),
    ]
    if operator_url.username:
        cli += ["-U", operator_url.username]
    maintenance_db = operator_url.database or "postgres"
    drill_url = operator_url.set(
        drivername="postgresql+asyncpg", database=database
    ).render_as_string(hide_password=False)
    env["DATABASE_URL"] = drill_url
    env["RUN_DB_TESTS"] = "1"
    env.setdefault("ALLOW_ALL_USERS", "true")
    created = False
    try:
        _run(
            [
                _pg_tool("createdb"),
                *cli,
                "--maintenance-db",
                maintenance_db,
                "--template",
                "dailyplanner_recovery_template",
                database,
            ],
            env,
        )
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
                    "--backup",
                    str(backup),
                ],
                env,
            )
    finally:
        if created:
            _run(
                [
                    _pg_tool("dropdb"),
                    *cli,
                    "--maintenance-db",
                    maintenance_db,
                    "--force",
                    database,
                ],
                env,
            )


if __name__ == "__main__":
    main()
