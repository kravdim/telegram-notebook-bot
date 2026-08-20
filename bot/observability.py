"""Lightweight in-process metrics plus persistent SLO evaluation."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
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

    def snapshot(self) -> dict:
        observations = {}
        for name, values in self._samples.items():
            if values:
                ordered = sorted(values)
                p95_index = max(0, int(len(ordered) * 0.95) - 1)
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
    if backup:
        backup_age_hours = max(
            0.0, (now - pendulum.instance(backup.updated_at)).total_hours()
        )
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
                else "ok" if backup_age_hours <= max_backup_age_hours
                else "error"
            ),
            "age_hours": round(backup_age_hours, 1) if backup_age_hours is not None else None,
            "target_hours": max_backup_age_hours,
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
