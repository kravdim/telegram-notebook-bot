"""Retention jobs для LLM-логов и временного состояния."""

import logging

import pendulum
from sqlalchemy import delete

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import FsmState, InteractionState, LlmLog, ProcessedRequest

logger = logging.getLogger(__name__)


async def rotate_llm_logs() -> None:
    """Удалить старые LLM-логи и transient state согласно privacy policy."""
    yaml_cfg = settings.yaml_config
    retention_days = yaml_cfg.get("scheduler", {}).get("llm_log_retention_days", 90)
    transient_days = yaml_cfg.get("scheduler", {}).get(
        "transient_state_retention_days", 30
    )

    cutoff = pendulum.now("UTC").subtract(days=retention_days)
    transient_cutoff = pendulum.now("UTC").subtract(days=transient_days)
    now = pendulum.now("UTC")

    async with async_session() as session:
        llm_result = await session.execute(
            delete(LlmLog).where(LlmLog.created_at < cutoff)
        )
        interaction_result = await session.execute(
            delete(InteractionState).where(
                InteractionState.expires_at.is_not(None),
                InteractionState.expires_at < now,
            )
        )
        request_result = await session.execute(
            delete(ProcessedRequest).where(
                ProcessedRequest.created_at < transient_cutoff
            )
        )
        fsm_result = await session.execute(
            delete(FsmState).where(FsmState.updated_at < transient_cutoff)
        )
        await session.commit()
        deleted = llm_result.rowcount
        transient_deleted = sum(
            result.rowcount or 0
            for result in (interaction_result, request_result, fsm_result)
        )

    if deleted:
        logger.info("Ротация llm_logs: удалено %d записей старше %d дней", deleted, retention_days)
    if transient_deleted:
        logger.info(
            "Retention transient state: удалено %d записей",
            transient_deleted,
        )
