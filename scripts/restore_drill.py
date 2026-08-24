#!/usr/bin/env python3
"""Restore a verified backup into an isolated database and record measured RTO."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url

DRILL_PREFIX = "dailyplanner_restore_drill_"
RECOVERY_TEMPLATE = "dailyplanner_recovery_template"


@dataclass(frozen=True)
class DrillResult:
    backup: str
    backup_bytes: int
    backup_sha256: str
    migration: str
    public_tables: int
    users: int
    tasks: int
    delivery_batches: int
    rto_seconds: float
    completed_at: str
    status: str = "ok"


def _run(command: list[str], env: dict[str, str], *, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        env=env,
        stdin=stdin,
        check=True,
        text=stdin is None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _verify_checksum(backup: Path) -> str:
    checksum = backup.with_suffix(backup.suffix + ".sha256")
    if not checksum.is_file():
        raise ValueError(f"checksum sidecar is missing: {checksum}")
    expected = checksum.read_text(encoding="ascii").split()[0]
    digest = hashlib.sha256()
    with backup.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError("backup checksum mismatch")
    return actual


def _latest_verified_backup(directory: Path, max_age_hours: float) -> Path:
    candidates = sorted(
        (
            path
            for path in directory.glob("notebook_bot_*.sql.gz")
            if path.with_suffix(path.suffix + ".sha256").is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise ValueError(f"no backup with checksum found in {directory}")
    backup = candidates[0]
    age_hours = (time.time() - backup.stat().st_mtime) / 3600
    if max_age_hours > 0 and age_hours > max_age_hours:
        raise ValueError(
            f"latest verified backup is stale: age={age_hours:.1f}h, limit={max_age_hours:.1f}h"
        )
    return backup


def _pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("/opt/homebrew/opt").glob(f"postgresql*/bin/{name}"), reverse=True)
    if candidates:
        return str(candidates[0])
    raise RuntimeError(f"required PostgreSQL client tool is missing: {name}")


def _connection_args(url: URL) -> list[str]:
    args = ["-h", url.host or "localhost", "-p", str(url.port or 5432)]
    if url.username:
        args += ["-U", url.username]
    return args


def _validate_operator(url: URL, env: dict[str, str]) -> None:
    query = (
        "SELECT rolcreatedb::int, rolsuper::int, rolcreaterole::int, "
        "rolreplication::int, (SELECT count(*) FROM pg_database "
        "WHERE datname='dailyplanner_recovery_template' "
        "AND datistemplate AND NOT datallowconn) "
        "FROM pg_roles WHERE rolname=current_user;"
    )
    result = _run(
        [
            _pg_tool("psql"),
            *_connection_args(url),
            "-At",
            "-d",
            url.database or "postgres",
            "-c",
            query,
        ],
        env,
    ).stdout.strip()
    if result != "1|0|0|0|1":
        raise ValueError(
            "operator role must be CREATEDB, NOSUPERUSER, NOCREATEROLE, "
            f"NOREPLICATION and template {RECOVERY_TEMPLATE} must be ready"
        )


def _append_report(path: Path, result: DrillResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as report:
        report.write(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n")


def run_restore_drill(
    backup: Path,
    operator_database_url: str,
    *,
    expected_revision: str | None = None,
) -> DrillResult:
    if not backup.is_file():
        raise ValueError(f"backup does not exist: {backup}")
    digest = _verify_checksum(backup)

    url = make_url(operator_database_url)
    if not url.drivername.startswith("postgresql") or not url.database:
        raise ValueError("OPERATOR_DATABASE_URL must be a PostgreSQL URL")
    if not url.username or not url.password:
        raise ValueError("OPERATOR_DATABASE_URL must contain operator credentials")

    env = os.environ.copy()
    env["PGPASSWORD"] = url.password
    _validate_operator(url, env)

    drill_db = f"{DRILL_PREFIX}{secrets.token_hex(6)}"
    connection_args = _connection_args(url)
    started = time.monotonic()
    created = False
    try:
        _run(
            [
                _pg_tool("createdb"),
                *connection_args,
                "--maintenance-db",
                url.database,
                "--template",
                RECOVERY_TEMPLATE,
                drill_db,
            ],
            env,
        )
        created = True
        decompressor = subprocess.Popen(
            ["gzip", "-dc", str(backup)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        restore = subprocess.run(
            [
                _pg_tool("psql"),
                *connection_args,
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                drill_db,
            ],
            env=env,
            stdin=decompressor.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if decompressor.stdout:
            decompressor.stdout.close()
        gzip_stderr = decompressor.communicate()[1]
        if decompressor.returncode:
            raise RuntimeError(
                "backup decompression failed: " + gzip_stderr.decode(errors="replace")[-2000:]
            )
        if restore.returncode:
            raise RuntimeError("restore failed: " + restore.stderr.decode(errors="replace")[-2000:])

        query = (
            "SELECT (SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public'), "
            "(SELECT version_num FROM alembic_version LIMIT 1), "
            "(SELECT count(*) FROM users), (SELECT count(*) FROM tasks), "
            "(SELECT count(*) FROM delivery_batches);"
        )
        validation = _run(
            [
                _pg_tool("psql"),
                *connection_args,
                "-At",
                "-d",
                drill_db,
                "-c",
                query,
            ],
            env,
        ).stdout.strip()
        values = validation.split("|", 4)
        if len(values) != 5:
            raise RuntimeError("restore validation returned an unexpected result")
        table_count_raw, restored_revision, users, tasks, batches = values
        expected = (
            expected_revision
            or ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        )
        if int(table_count_raw) < 1:
            raise RuntimeError("restored database has no public tables")
        if restored_revision != expected:
            raise RuntimeError(
                f"restored migration mismatch: {restored_revision!r} != {expected!r}"
            )
        elapsed = time.monotonic() - started
        return DrillResult(
            backup=backup.name,
            backup_bytes=backup.stat().st_size,
            backup_sha256=digest,
            migration=restored_revision,
            public_tables=int(table_count_raw),
            users=int(users),
            tasks=int(tasks),
            delivery_batches=int(batches),
            rto_seconds=round(elapsed, 2),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        if created:
            _run(
                [
                    _pg_tool("dropdb"),
                    *connection_args,
                    "--maintenance-db",
                    url.database,
                    "--force",
                    drill_db,
                ],
                env,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--backup", type=Path)
    source.add_argument("--latest-backup-dir", type=Path)
    parser.add_argument(
        "--operator-database-url",
        default=os.environ.get("OPERATOR_DATABASE_URL"),
    )
    parser.add_argument("--max-backup-age-hours", type=float, default=30)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expected-revision",
        help="Expected Alembic revision; defaults to the current code head",
    )
    args = parser.parse_args()
    if not args.operator_database_url:
        raise SystemExit("OPERATOR_DATABASE_URL or --operator-database-url is required")
    try:
        backup = args.backup or _latest_verified_backup(
            args.latest_backup_dir, args.max_backup_age_hours
        )
        result = run_restore_drill(
            backup,
            args.operator_database_url,
            expected_revision=args.expected_revision,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.report:
        _append_report(args.report, result)
    print(
        f"restore drill ok: backup={result.backup}, tables={result.public_tables}, "
        f"migration={result.migration}, users={result.users}, tasks={result.tasks}, "
        f"rto_seconds={result.rto_seconds:.2f}"
    )


if __name__ == "__main__":
    main()
