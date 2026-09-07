"""Регрессии реальных файловых и subprocess-границ из ревью 07.09.2026."""

import gzip
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.scheduler import backup
from bot.services.access_config import read_allowed_telegram_ids, write_allowed_telegram_ids

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [70_000, 1_000_000])
async def test_backup_preserves_long_copy_rows_and_filters_client_setting(tmp_path, size):
    output = tmp_path / "dump.gz"
    statement = b"SET transaction_timeout = 0;\n"
    payload = b"COPY test FROM stdin;\n" + statement + b"x" * size + b"\n\\.\n"
    source = tmp_path / "source.sql"
    source.write_bytes(statement + payload)
    evidence = await backup._stream_backup(
        output, [sys.executable, "-c", "import sys;sys.stdout.buffer.write(open(sys.argv[1],'rb').read())", str(source)],
        os.environ.copy(),
    )
    assert evidence[:2] == (0, 0)
    assert gzip.decompress(output.read_bytes()) == payload
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_failed_backup_keeps_last_old_copy_and_removes_partial(tmp_path, monkeypatch):
    previous = tmp_path / "notebook_bot_old.sql.gz"
    previous.write_bytes(b"last recovery point")
    os.utime(previous, (1, 1))
    monkeypatch.setattr(backup, "_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup, "settings", SimpleNamespace(yaml_config={}))
    monkeypatch.setattr(backup, "_dump_command_and_env", lambda: ([], {}))

    async def fail(path, *args):
        path.write_bytes(b"partial")
        return 1, 0, b"failure", b""

    monkeypatch.setattr(backup, "_stream_backup", fail)
    assert await backup.run_backup() is None
    assert list(tmp_path.iterdir()) == [previous]


@pytest.mark.asyncio
async def test_successful_backup_publishes_archive_then_rotates(tmp_path, monkeypatch):
    previous = tmp_path / "notebook_bot_old.sql.gz"
    previous.write_bytes(b"old")
    os.utime(previous, (1, 1))
    monkeypatch.setattr(backup, "_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup, "settings", SimpleNamespace(yaml_config={}))
    monkeypatch.setattr(backup, "_dump_command_and_env", lambda: ([], {}))
    monkeypatch.setattr(backup, "_record_backup_success", AsyncMock())

    async def succeed(path, *args):
        assert path.name.startswith(".")
        path.write_bytes(gzip.compress(b"complete"))
        return 0, 0, b"", b""

    monkeypatch.setattr(backup, "_stream_backup", succeed)
    result = await backup.run_backup()
    assert result is not None
    assert gzip.decompress(result.read_bytes()) == b"complete"
    assert not previous.exists()
    assert list(tmp_path.iterdir()) == [result]


def test_revocation_survives_release_switch_and_rollback(tmp_path):
    shared = tmp_path / "shared.yaml"
    write_allowed_telegram_ids(shared, [1, 2])
    releases = [tmp_path / "old.yaml", tmp_path / "new.yaml"]
    for release in releases:
        release.symlink_to(shared)
    write_allowed_telegram_ids(releases[0], [1])
    assert all(release.is_symlink() for release in releases)
    assert all(read_allowed_telegram_ids(release) == [1] for release in releases)
    assert read_allowed_telegram_ids(shared) == [1]


def test_migration_helper_reads_target_release_independent_of_cwd(tmp_path):
    versions = tmp_path / "bot/db/migrations/versions"
    versions.mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = bot/db/migrations\n")
    (versions / "old.py").write_text("revision = 'synthetic_old'\ndown_revision = None\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/get_migration_head.py"), "--project", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "synthetic_old"


def test_restore_rejects_corrupt_gzip_before_psql(tmp_path):
    archive = tmp_path / "bad.sql.gz"
    archive.write_bytes(b"not gzip")
    archive.with_suffix(".gz.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    stub = tmp_path / "psql"
    marker = tmp_path / "psql-called"
    stub.write_text('#!/bin/sh\ntouch "$REVIEW_PSQL_MARKER"\ncat >/dev/null\n')
    stub.chmod(0o700)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "DATABASE_URL": "postgresql://synthetic", "REVIEW_PSQL_MARKER": str(marker)}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/restore_backup.sh"), str(archive)], input="RESTORE\n",
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Restore completed" not in result.stdout
    assert not marker.exists()
