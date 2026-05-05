"""Ежедневный бэкап БД через pg_dump + ротация."""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pendulum

from bot.config import settings

logger = logging.getLogger(__name__)

_BACKUP_DIR = Path.home() / "backups" / "notebook-bot"


async def run_backup() -> None:
    """Выполнить pg_dump и удалить старые бэкапы."""
    yaml_cfg = settings.yaml_config
    retention_days = yaml_cfg.get("scheduler", {}).get("backup_retention_days", 30)

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    now = pendulum.now()
    filename = f"notebook_bot_{now.format('YYYY-MM-DD_HHmm')}.sql.gz"
    filepath = _BACKUP_DIR / filename

    # Парсим DATABASE_URL для pg_dump
    db_url = settings.database_url
    try:
        parts = db_url.replace("postgresql+asyncpg://", "").split("@")
        user_pass = parts[0]
        host_db = parts[1]
        user, password = user_pass.split(":", 1)
        host_port, dbname = host_db.split("/", 1)
        host = host_port.split(":")[0]
        port = host_port.split(":")[1] if ":" in host_port else "5432"
    except (IndexError, ValueError) as e:
        logger.error("Не удалось распарсить DATABASE_URL: %s", e)
        return

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    try:
        pg_dump = await asyncio.create_subprocess_exec(
            "pg_dump", "-h", host, "-p", port, "-U", user, "-d", dbname,
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
            if pg_dump.stdout and gzip_proc.stdin:
                while chunk := await pg_dump.stdout.read(1024 * 1024):
                    gzip_proc.stdin.write(chunk)
                    await gzip_proc.stdin.drain()
                gzip_proc.stdin.close()
                await gzip_proc.stdin.wait_closed()

            await asyncio.wait_for(gzip_proc.wait(), timeout=300)
            await asyncio.wait_for(pg_dump.wait(), timeout=10)

        if pg_dump.returncode != 0:
            stderr = (await pg_dump.stderr.read()).decode() if pg_dump.stderr else ""
            logger.error("pg_dump failed (rc=%d): %s", pg_dump.returncode, stderr)
            filepath.unlink(missing_ok=True)
        elif gzip_proc.returncode != 0:
            gzip_err = (await gzip_proc.stderr.read()).decode() if gzip_proc.stderr else ""
            logger.error("gzip failed: %s", gzip_err)
            filepath.unlink(missing_ok=True)
        else:
            size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info("Бэкап создан: %s (%.1f MB)", filename, size_mb)
    except asyncio.TimeoutError:
        logger.error("Таймаут бэкапа")
        filepath.unlink(missing_ok=True)
    except Exception as e:
        logger.error("Ошибка бэкапа: %s", e)
        filepath.unlink(missing_ok=True)

    # Ротация
    _rotate_backups(retention_days)


def _rotate_backups(retention_days: int) -> None:
    """Удалить бэкапы старше retention_days."""
    if not _BACKUP_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    for f in _BACKUP_DIR.glob("notebook_bot_*.sql.gz"):
        if f.stat().st_mtime < cutoff.timestamp():
            f.unlink()
            logger.info("Удалён старый бэкап: %s", f.name)
