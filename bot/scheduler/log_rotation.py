"""Ротация llm_logs: удаление записей старше N дней."""

import logging

import pendulum
from sqlalchemy import delete

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import LlmLog

logger = logging.getLogger(__name__)


async def rotate_llm_logs() -> None:
    """Удалить старые записи llm_logs."""
    yaml_cfg = settings.yaml_config
    retention_days = yaml_cfg.get("scheduler", {}).get("llm_log_retention_days", 90)

    cutoff = pendulum.now("UTC").subtract(days=retention_days)

    async with async_session() as session:
        result = await session.execute(
            delete(LlmLog).where(LlmLog.created_at < cutoff)
        )
        await session.commit()
        deleted = result.rowcount

    if deleted:
        logger.info("Ротация llm_logs: удалено %d записей старше %d дней", deleted, retention_days)
