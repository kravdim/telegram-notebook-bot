"""Lightweight in-process metrics plus persistent SLO evaluation."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import pendulum
from sqlalchemy import func, select

from bot.config import settings
from bot.db.crud.operational import get_operational_state, set_operational_state
from bot.db.engine import async_session
from bot.db.models import Reminder

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiogram import Bot


class TelegramConflictAlertHandler(logging.Handler):
    """Convert aiogram getUpdates conflicts into a metric and admin alert."""

    def __init__(self, bot: "Bot", loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.loop = loop
        self._last_alert_scheduled = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if "TelegramConflictError" not in record.getMessage():
            return
        metrics.increment("telegram.polling_conflict")
        now = time.monotonic()
        if now - self._last_alert_scheduled < 60:
            return
        self._last_alert_scheduled = now
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            _alert_telegram_conflict(self.bot),
        )


async def _alert_telegram_conflict(bot: "Bot") -> None:
    """Alert at most once per hour for a competing getUpdates poller."""
    marker_key = "telegram.polling_conflict"
    try:
        async with async_session() as session:
            previous = await get_operational_state(session, marker_key)
            if previous and pendulum.instance(previous.updated_at) > pendulum.now(
                "UTC"
            ).subtract(hours=1):
                return
    except Exception as exc:
        logger.error("Polling conflict throttle lookup failed: %s", exc)

    delivered = False
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(
                admin_id,
                "🚨 DailyPlanner: обнаружен второй Telegram getUpdates-поллер. "
                "Проверь VPS и локальные экземпляры бота.",
            )
            delivered = True
        except Exception as exc:
            logger.error("Polling conflict alert failed for %s: %s", admin_id, exc)
    if delivered:
        try:
            async with async_session() as session:
                await set_operational_state(
                    session,
                    marker_key,
                    {"detected_at": pendulum.now("UTC").to_iso8601_string()},
                )
        except Exception as exc:
            logger.error("Polling conflict throttle update failed: %s", exc)


def install_telegram_conflict_alert(
    bot: "Bot", loop: asyncio.AbstractEventLoop
) -> TelegramConflictAlertHandler:
    handler = TelegramConflictAlertHandler(bot, loop)
    logging.getLogger("aiogram.dispatcher").addHandler(handler)
    return handler


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))

    def increment(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        self._samples[name].append(value)

    def latest(self, name: str) -> float | None:
        """Return the latest observation without exposing mutable storage."""
        values = self._samples.get(name)
        return values[-1] if values else None

    def snapshot(self) -> dict:
        observations = {}
        for name, values in self._samples.items():
            if values:
                ordered = sorted(values)
                p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
                observations[name] = {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 3),
                    "p95": round(ordered[p95_index], 3),
                    "max": round(max(values), 3),
                }
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "observations": observations,
        }


metrics = MetricsRegistry()


def backup_artifact_status(value: dict) -> tuple[bool, str]:
    """Verify that the persisted backup marker still points to its archive."""
    filename = value.get("file")
    expected_bytes = value.get("bytes")
    if not isinstance(filename, str) or Path(filename).name != filename:
        return False, "invalid-marker"
    if not isinstance(expected_bytes, int) or expected_bytes <= 0:
        return False, "invalid-marker"
    backup_dir = Path(
        os.environ.get("BACKUP_DIR", str(Path.home() / "backups" / "notebook-bot"))
    )
    archive = backup_dir / filename
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if not archive.is_file():
        return False, "archive-missing"
    if archive.stat().st_size != expected_bytes:
        return False, "size-mismatch"
    if not checksum.is_file():
        return False, "checksum-missing"
    try:
        digest, separator, listed_name = checksum.read_text(
            encoding="ascii"
        ).strip().partition("  ")
    except (OSError, UnicodeError):
        return False, "checksum-marker-invalid"
    if (
        separator != "  "
        or listed_name != filename
        or len(digest) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in digest)
    ):
        return False, "checksum-marker-invalid"
    return True, "ok"


@asynccontextmanager
async def observe_job(name: str) -> AsyncIterator[None]:
    started = time.monotonic()
    try:
        yield
        metrics.increment(f"scheduler.{name}.success")
    except Exception:
        metrics.increment(f"scheduler.{name}.error")
        raise
    finally:
        metrics.observe(f"scheduler.{name}.duration_seconds", time.monotonic() - started)


async def evaluate_slos() -> dict[str, dict[str, object]]:
    """Evaluate reminder lag and backup freshness from persistent state."""
    slo_cfg = settings.yaml_config.get("slo", {})
    max_reminder_lag = int(slo_cfg.get("reminder_lag_seconds", 120))
    max_backup_age_hours = int(slo_cfg.get("backup_max_age_hours", 30))
    now = pendulum.now("UTC")

    async with async_session() as session:
        query_result = await session.execute(
            select(func.min(Reminder.remind_at), func.count(Reminder.id)).where(
                Reminder.is_sent.is_(False),
                Reminder.remind_at <= now,
            )
        )
        oldest, pending_count = query_result.one()
        backup = await get_operational_state(session, "backup.last_success")

    reminder_lag = 0.0
    if oldest:
        reminder_lag = max(0.0, (now - pendulum.instance(oldest)).total_seconds())
    metrics.gauge("reminders.pending", float(pending_count or 0))
    metrics.gauge("reminders.oldest_lag_seconds", reminder_lag)

    backup_age_hours = None
    artifact_ok = False
    artifact_status = "marker-missing"
    if backup:
        backup_age_hours = max(
            0.0, (now - pendulum.instance(backup.updated_at)).total_hours()
        )
        artifact_ok, artifact_status = backup_artifact_status(backup.value)
        metrics.gauge("backup.age_hours", backup_age_hours)

    slo_result: dict[str, dict[str, object]] = {
        "reminders": {
            "status": "ok" if reminder_lag <= max_reminder_lag else "error",
            "lag_seconds": round(reminder_lag, 1),
            "pending": int(pending_count or 0),
            "target_seconds": max_reminder_lag,
        },
        "backup": {
            "status": (
                "unknown" if backup_age_hours is None
                else "ok" if backup_age_hours <= max_backup_age_hours and artifact_ok
                else "error"
            ),
            "age_hours": round(backup_age_hours, 1) if backup_age_hours is not None else None,
            "target_hours": max_backup_age_hours,
            "artifact": artifact_status,
        },
    }
    for name, info in slo_result.items():
        if info["status"] == "error":
            logger.error("SLO violation: %s=%s", name, info)
    return slo_result


async def alert_slo_violations(
    bot: "Bot", slo_result: dict[str, dict[str, object]]
) -> None:
    """Send throttled Telegram alerts to admins when an SLO is violated."""
    for name, info in slo_result.items():
        if info.get("status") != "error":
            continue
        marker_key = f"slo.alert.{name}"
        async with async_session() as session:
            previous = await get_operational_state(session, marker_key)
            if previous and pendulum.instance(previous.updated_at) > pendulum.now(
                "UTC"
            ).subtract(hours=1):
                continue
        message = f"🚨 DailyPlanner SLO: {name}\n{info}"
        delivered = False
        for admin_id in settings.admin_telegram_ids:
            try:
                await bot.send_message(admin_id, message)
                delivered = True
            except Exception as exc:
                logger.error("SLO alert delivery failed for %s: %s", admin_id, exc)
        if delivered:
            async with async_session() as session:
                await set_operational_state(session, marker_key, info)
