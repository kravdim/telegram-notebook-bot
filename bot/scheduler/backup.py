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

logger = logging.getLogger(__name__)

_BACKUP_DIR = Path(
    os.environ.get("BACKUP_DIR", str(Path.home() / "backups" / "notebook-bot"))
)


async def run_backup() -> None:
    """Выполнить pg_dump и удалить старые бэкапы."""
    yaml_cfg = settings.yaml_config
    retention_days = yaml_cfg.get("scheduler", {}).get("backup_retention_days", 30)

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    now = pendulum.now()
    filename = f"notebook_bot_{now.format('YYYY-MM-DD_HHmm')}.sql.gz"
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
        return

    pg_dump_bin = _find_pg_dump()
    if not pg_dump_bin:
        logger.error(
            "pg_dump не найден; установите PostgreSQL client или задайте PG_DUMP_BIN"
        )
        return

    env = os.environ.copy()
    env["PGPASSWORD"] = password

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
            digest = hashlib.sha256()
            with open(filepath, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksum_path = filepath.with_suffix(filepath.suffix + ".sha256")
            checksum_path.write_text(
                f"{digest.hexdigest()}  {filepath.name}\n", encoding="ascii"
            )
            logger.info("Бэкап создан: %s (%.1f MB)", filename, size_mb)
    except asyncio.TimeoutError:
        logger.error("Таймаут бэкапа")
        filepath.unlink(missing_ok=True)
    except Exception as e:
        logger.error("Ошибка бэкапа: %s", e)
        filepath.unlink(missing_ok=True)

    # Ротация
    _rotate_backups(retention_days)


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
