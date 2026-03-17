"""Health check LLM main провайдера каждые 5 минут."""

import logging

logger = logging.getLogger(__name__)


async def check_llm_health(llm_client) -> None:
    """Проверить доступность main LLM и восстановить при необходимости."""
    healthy = await llm_client.health_check()
    if healthy:
        logger.debug("Main LLM health check: OK")
    else:
        logger.warning("Main LLM health check: FAILED, using fallback")
