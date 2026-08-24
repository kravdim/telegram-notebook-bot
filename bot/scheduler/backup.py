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
from bot.db.crud.operational import set_operational_state
from bot.db.engine import async_session
from bot.observability import metrics

logger = logging.getLogger(__name__)

_BACKUP_DIR = Path(
    os.environ.get("BACKUP_DIR", str(Path.home() / "backups" / "notebook-bot"))
)


async def run_backup() -> Path | None:
    """Выполнить pg_dump и удалить старые бэкапы."""
    yaml_cfg = settings.yaml_config
    retention_days = yaml_cfg.get("scheduler", {}).get("backup_retention_days", 30)

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    now = pendulum.now()
    filename = f"notebook_bot_{now.format('YYYY-MM-DD_HHmmss')}.sql.gz"
    filepath = _BACKUP_DIR / filename

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
        logger.error("Не удалось распарсить DATABASE_URL: %s", e)
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

    pg_dump = None
    gzip_proc = None
    pg_stderr_task = None
    gzip_stderr_task = None
    try:
        pg_dump = await asyncio.create_subprocess_exec(
            pg_dump_bin, "-h", host, "-p", port, "-U", user, "-d", dbname,
            "--no-owner", "--no-acl",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        with open(filepath, "wb") as backup_file:
            gzip_proc = await asyncio.create_subprocess_exec(
                "gzip",
                stdin=asyncio.subprocess.PIPE,
                stdout=backup_file,
                stderr=asyncio.subprocess.PIPE,
            )
            pg_stderr_task = asyncio.create_task(pg_dump.stderr.read())
            gzip_stderr_task = asyncio.create_task(gzip_proc.stderr.read())
            async with asyncio.timeout(300):
                if pg_dump.stdout and gzip_proc.stdin:
                    while line := await pg_dump.stdout.readline():
                        # pg_dump 17 emits this setting even when dumping an older
                        # server; PostgreSQL <=16 rejects it during restore.
                        if not _is_portable_dump_line(line):
                            continue
                        gzip_proc.stdin.write(line)
                        await gzip_proc.stdin.drain()
                    gzip_proc.stdin.close()
                    await gzip_proc.stdin.wait_closed()

                await asyncio.gather(pg_dump.wait(), gzip_proc.wait())
                pg_stderr, gzip_stderr = await asyncio.gather(
                    pg_stderr_task, gzip_stderr_task
                )

        if pg_dump.returncode != 0:
            logger.error(
                "pg_dump failed (rc=%d): %s",
                pg_dump.returncode,
                pg_stderr.decode(errors="replace"),
            )
            filepath.unlink(missing_ok=True)
            metrics.increment("backup.error")
        elif gzip_proc.returncode != 0:
            logger.error("gzip failed: %s", gzip_stderr.decode(errors="replace"))
            filepath.unlink(missing_ok=True)
            metrics.increment("backup.error")
        else:
            size_mb = filepath.stat().st_size / (1024 * 1024)
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
                        {"file": filepath.name, "bytes": filepath.stat().st_size},
                    )
            except Exception as state_error:
                # The archive is still valid. Do not delete it merely because the
                # observability marker could not be persisted.
                logger.error("Бэкап создан, но SLO-маркер не сохранён: %s", state_error)
            metrics.increment("backup.success")
            logger.info("Бэкап создан: %s (%.1f MB)", filename, size_mb)
    except asyncio.TimeoutError:
        logger.error("Таймаут бэкапа")
        for process in (pg_dump, gzip_proc):
            if process and process.returncode is None:
                process.kill()
        await asyncio.gather(
            *(process.wait() for process in (pg_dump, gzip_proc) if process),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(task for task in (pg_stderr_task, gzip_stderr_task) if task),
            return_exceptions=True,
        )
        filepath.unlink(missing_ok=True)
        metrics.increment("backup.error")
    except Exception as e:
        logger.error("Ошибка бэкапа: %s", e)
        for process in (pg_dump, gzip_proc):
            if process and process.returncode is None:
                process.kill()
        await asyncio.gather(
            *(process.wait() for process in (pg_dump, gzip_proc) if process),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(task for task in (pg_stderr_task, gzip_stderr_task) if task),
            return_exceptions=True,
        )
        filepath.unlink(missing_ok=True)
        metrics.increment("backup.error")

    # Ротация
    _rotate_backups(retention_days)
    return filepath if filepath.exists() else None


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
