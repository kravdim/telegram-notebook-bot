"""Ежедневный бэкап БД через pg_dump + ротация."""

import asyncio
import hashlib
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pendulum
from sqlalchemy.engine import make_url

from bot.config import settings
from bot.db.crud.operational import get_operational_state, set_operational_state
from bot.db.engine import async_session
from bot.logging_safety import error_type
from bot.observability import metrics

logger = logging.getLogger(__name__)

_BACKUP_DIR = Path(
    os.environ.get("BACKUP_DIR", str(Path.home() / "backups" / "notebook-bot"))
)


def _dump_command_and_env() -> tuple[list[str], dict[str, str]] | None:
    """Build a secret-safe pg_dump invocation from the configured database URL."""
    try:
        db_url = make_url(settings.database_url)
        user = db_url.username or ""
        password = db_url.password or ""
        host = db_url.host or "localhost"
        port = str(db_url.port or 5432)
        dbname = db_url.database or ""
        if not user or not dbname:
            raise ValueError("username or database is empty")
    except (TypeError, ValueError) as e:
        logger.error(
            "Не удалось распарсить DATABASE_URL: error_type=%s", error_type(e)
        )
        metrics.increment("backup.error")
        return None
    pg_dump_bin = _find_pg_dump()
    if not pg_dump_bin:
        logger.error(
            "pg_dump не найден; установите PostgreSQL client или задайте PG_DUMP_BIN"
        )
        metrics.increment("backup.error")
        return None
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    command = [
        pg_dump_bin,
        "-h", host,
        "-p", port,
        "-U", user,
        "-d", dbname,
        "--no-owner",
        "--no-acl",
    ]
    return command, env


async def _stop_pipeline(processes: tuple, stderr_tasks: tuple) -> None:
    for process in processes:
        if process and process.returncode is None:
            process.kill()
    await asyncio.gather(
        *(process.wait() for process in processes if process),
        return_exceptions=True,
    )
    await asyncio.gather(
        *(task for task in stderr_tasks if task),
        return_exceptions=True,
    )


async def _stream_backup(filepath: Path, command: list[str], env: dict[str, str]):
    """Pipe a portable pg_dump stream through gzip and return process evidence."""
    pg_dump = gzip_proc = pg_stderr_task = gzip_stderr_task = None
    try:
        pg_dump = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if pg_dump.stdout is None or pg_dump.stderr is None:
            raise RuntimeError("pg_dump pipes were not created")
        with open(filepath, "wb") as backup_file:
            gzip_proc = await asyncio.create_subprocess_exec(
                "gzip",
                stdin=asyncio.subprocess.PIPE,
                stdout=backup_file,
                stderr=asyncio.subprocess.PIPE,
            )
            if gzip_proc.stdin is None or gzip_proc.stderr is None:
                raise RuntimeError("gzip pipes were not created")
            pg_stderr_task = asyncio.create_task(pg_dump.stderr.read())
            gzip_stderr_task = asyncio.create_task(gzip_proc.stderr.read())
            async with asyncio.timeout(300):
                while line := await pg_dump.stdout.readline():
                    if _is_portable_dump_line(line):
                        gzip_proc.stdin.write(line)
                        await gzip_proc.stdin.drain()
                gzip_proc.stdin.close()
                await gzip_proc.stdin.wait_closed()
                await asyncio.gather(pg_dump.wait(), gzip_proc.wait())
                pg_stderr, gzip_stderr = await asyncio.gather(
                    pg_stderr_task, gzip_stderr_task
                )
        return pg_dump.returncode, gzip_proc.returncode, pg_stderr, gzip_stderr
    except BaseException:
        await _stop_pipeline(
            (pg_dump, gzip_proc), (pg_stderr_task, gzip_stderr_task)
        )
        raise


async def _record_backup_success(filepath: Path) -> None:
    digest = hashlib.sha256()
    with open(filepath, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = filepath.with_suffix(filepath.suffix + ".sha256")
    checksum_path.write_text(
        f"{digest.hexdigest()}  {filepath.name}\n", encoding="ascii"
    )
    try:
        async with async_session() as session:
            await set_operational_state(
                session,
                "backup.last_success",
                {
                    "file": filepath.name,
                    "bytes": filepath.stat().st_size,
                    "sha256": digest.hexdigest(),
                    "checksum_verified_at": pendulum.now("UTC").to_iso8601_string(),
                },
            )
    except Exception as state_error:
        logger.error(
            "Бэкап создан, но SLO-маркер не сохранён: error_type=%s",
            error_type(state_error),
        )
    metrics.increment("backup.success")
    size_mb = filepath.stat().st_size / (1024 * 1024)
    logger.info("Бэкап создан: %s (%.1f MB)", filepath.name, size_mb)


async def _finalize_backup(filepath: Path, evidence) -> bool:
    pg_returncode, gzip_returncode, pg_stderr, gzip_stderr = evidence
    if pg_returncode != 0:
        logger.error("pg_dump failed: rc=%d stderr_bytes=%d", pg_returncode, len(pg_stderr))
    elif gzip_returncode != 0:
        logger.error("gzip failed: rc=%d stderr_bytes=%d", gzip_returncode, len(gzip_stderr))
    else:
        await _record_backup_success(filepath)
        return True
    filepath.unlink(missing_ok=True)
    metrics.increment("backup.error")
    return False


async def run_backup() -> Path | None:
    """Выполнить pg_dump и удалить старые бэкапы."""
    retention_days = settings.yaml_config.get("scheduler", {}).get(
        "backup_retention_days", 30
    )
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"notebook_bot_{pendulum.now().format('YYYY-MM-DD_HHmmss')}.sql.gz"
    filepath = _BACKUP_DIR / filename
    invocation = _dump_command_and_env()
    if invocation is None:
        return None

    try:
        evidence = await _stream_backup(filepath, *invocation)
        await _finalize_backup(filepath, evidence)
    except asyncio.TimeoutError:
        logger.error("Таймаут бэкапа")
        filepath.unlink(missing_ok=True)
        metrics.increment("backup.error")
    except Exception as e:
        logger.error("Ошибка бэкапа: error_type=%s", error_type(e))
        filepath.unlink(missing_ok=True)
        metrics.increment("backup.error")
    _rotate_backups(retention_days)
    return filepath if filepath.exists() else None


def is_backup_due(
    last_success_at,
    now: pendulum.DateTime,
    backup_hour: int,
) -> bool:
    """Return whether today's persisted backup slot has been missed."""
    scheduled = now.start_of("day").add(hours=backup_hour)
    if now < scheduled:
        return False
    if last_success_at is None:
        return True
    last_local = pendulum.instance(last_success_at).in_tz(now.timezone)
    return last_local.date() < now.date()


async def run_backup_if_due(now: pendulum.DateTime | None = None) -> Path | None:
    """Run the daily backup after its slot, including catch-up after downtime."""
    current = now or pendulum.now()
    backup_hour = int(
        settings.yaml_config.get("scheduler", {}).get("backup_hour", 3)
    )
    async with async_session() as session:
        marker = await get_operational_state(session, "backup.last_success")
    if not is_backup_due(marker.updated_at if marker else None, current, backup_hour):
        return None
    return await run_backup()


def _is_portable_dump_line(line: bytes) -> bool:
    """Filter client-version settings rejected by older supported servers."""
    return line.strip() != b"SET transaction_timeout = 0;"


def _find_pg_dump() -> str | None:
    """Найти pg_dump в PATH, явной настройке или keg-only Homebrew."""
    configured = os.environ.get("PG_DUMP_BIN")
    if configured and Path(configured).is_file():
        return configured
    in_path = shutil.which("pg_dump")
    if in_path:
        return in_path
    homebrew_opt = Path("/opt/homebrew/opt")
    if homebrew_opt.exists():
        candidates = sorted(
            homebrew_opt.glob("postgresql*/bin/pg_dump"), reverse=True
        )
        if candidates:
            return str(candidates[0])
    return None


def _rotate_backups(retention_days: int) -> None:
    """Удалить бэкапы старше retention_days."""
    if not _BACKUP_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    for f in _BACKUP_DIR.glob("notebook_bot_*.sql.gz"):
        if f.stat().st_mtime < cutoff.timestamp():
            f.with_suffix(f.suffix + ".sha256").unlink(missing_ok=True)
            f.unlink()
            logger.info("Удалён старый бэкап: %s", f.name)
