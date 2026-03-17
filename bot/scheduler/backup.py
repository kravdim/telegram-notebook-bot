"""Ежедневный бэкап БД через pg_dump + ротация."""

import logging
import os
import subprocess
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
    # postgresql+asyncpg://user:pass@host:port/dbname
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

    cmd = (
        f"pg_dump -h {host} -p {port} -U {user} -d {dbname} "
        f"--no-owner --no-acl | gzip > {filepath}"
    )

    try:
        result = subprocess.run(
            cmd, shell=True, env=env,
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info("Бэкап создан: %s (%.1f MB)", filename, size_mb)
        else:
            logger.error("pg_dump failed: %s", result.stderr)
    except Exception as e:
        logger.error("Ошибка бэкапа: %s", e)

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
