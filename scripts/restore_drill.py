#!/usr/bin/env python3
"""Restore a gzip pg_dump into an isolated throw-away database and validate it."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


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


def _verify_checksum(backup: Path) -> None:
    checksum = backup.with_suffix(backup.suffix + ".sha256")
    if not checksum.is_file():
        raise SystemExit(f"checksum sidecar is missing: {checksum}")
    expected = checksum.read_text(encoding="ascii").split()[0]
    digest = hashlib.sha256()
    with backup.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit("backup checksum mismatch")


def _pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("/opt/homebrew/opt").glob(f"postgresql*/bin/{name}"), reverse=True)
    if candidates:
        return str(candidates[0])
    raise SystemExit(f"required PostgreSQL client tool is missing: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--expected-revision",
        help="Expected Alembic revision; defaults to the current code head",
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    if not args.backup.is_file():
        raise SystemExit(f"backup does not exist: {args.backup}")
    _verify_checksum(args.backup)

    url = make_url(args.database_url)
    if not url.drivername.startswith("postgresql") or not url.database:
        raise SystemExit("restore drill supports PostgreSQL URLs only")

    drill_db = f"dailyplanner_restore_drill_{secrets.token_hex(6)}"
    env = os.environ.copy()
    env["PGPASSWORD"] = url.password or ""
    connection_args = ["-h", url.host or "localhost", "-p", str(url.port or 5432)]
    if url.username:
        connection_args += ["-U", url.username]

    started = time.monotonic()
    created = False
    try:
        _run([_pg_tool("createdb"), *connection_args, drill_db], env)
        created = True
        decompressor = subprocess.Popen(
            ["gzip", "-dc", str(args.backup)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        restore = subprocess.run(
            [_pg_tool("psql"), *connection_args, "-v", "ON_ERROR_STOP=1", "-d", drill_db],
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
            raise SystemExit(
                "backup decompression failed: "
                + gzip_stderr.decode(errors="replace")[-2000:]
            )
        if restore.returncode:
            raise SystemExit("restore failed: " + restore.stderr.decode(errors="replace")[-2000:])

        query = (
            "SELECT (SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public'), "
            "(SELECT version_num FROM alembic_version LIMIT 1);"
        )
        validation = _run(
            [_pg_tool("psql"), *connection_args, "-At", "-d", drill_db, "-c", query], env
        ).stdout.strip()
        table_count_raw, restored_revision = validation.split("|", 1)
        expected_revision = args.expected_revision or ScriptDirectory.from_config(
            Config("alembic.ini")
        ).get_current_head()
        if int(table_count_raw) < 1:
            raise SystemExit("restored database has no public tables")
        if restored_revision != expected_revision:
            raise SystemExit(
                f"restored migration mismatch: {restored_revision!r} != {expected_revision!r}"
            )
        elapsed = time.monotonic() - started
        print(
            f"restore drill ok: database={drill_db}, tables={table_count_raw}, "
            f"migration={restored_revision}, rto_seconds={elapsed:.2f}"
        )
    finally:
        if created:
            _run([_pg_tool("dropdb"), *connection_args, "--force", drill_db], env)


if __name__ == "__main__":
    main()
