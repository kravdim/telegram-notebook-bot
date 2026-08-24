"""Расширенный health check: LLM, DB, Ollama."""

import logging
import time

from sqlalchemy import text

from bot.config import settings
from bot.db.engine import async_session
from bot.embeddings.indexer import get_client as get_embed_client
from bot.handlers.voice import get_client as get_stt_client
from bot.observability import evaluate_slos, metrics

logger = logging.getLogger(__name__)


async def check_stt_health() -> dict[str, object]:
    """Check the shared STT client and its latest observed user latency."""
    stt_client = get_stt_client()
    if not stt_client:
        return {"status": "not_configured"}
    try:
        start = time.monotonic()
        healthy = await stt_client.health_check()
        health_ms = int((time.monotonic() - start) * 1000)
        last_seconds = metrics.latest("stt.transcription_seconds")
        target_seconds = float(
            settings.yaml_config.get("slo", {}).get("stt_latency_seconds", 30)
        )
        status = "ok" if healthy else "error"
        if healthy and last_seconds is not None and last_seconds > target_seconds:
            status = "degraded"
        return {
            "status": status,
            "latency_ms": health_ms,
            "last_transcription_ms": (
                round(last_seconds * 1000) if last_seconds is not None else None
            ),
            "target_ms": round(target_seconds * 1000),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def check_llm_health(llm_client) -> None:
    """Проверка доступности LLM провайдера."""
    healthy = await llm_client.health_check()
    if healthy:
        logger.debug("LLM health check: OK")
    else:
        logger.warning("LLM health check: FAILED")


async def check_all_health(llm_client) -> dict:
    """Полная проверка всех сервисов. Возвращает статусы."""
    results = {}

    # PostgreSQL
    try:
        start = time.monotonic()
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        ms = int((time.monotonic() - start) * 1000)
        results["postgresql"] = {"status": "ok", "latency_ms": ms}
    except Exception as e:
        results["postgresql"] = {"status": "error", "error": str(e)}

    # LLM
    try:
        start = time.monotonic()
        healthy = await llm_client.health_check()
        ms = int((time.monotonic() - start) * 1000)
        results["llm"] = {
            "status": "ok" if healthy else "degraded",
            "latency_ms": ms,
            "main_healthy": llm_client._main_healthy,
        }
    except Exception as e:
        results["llm"] = {"status": "error", "error": str(e)}

    # Embedding (Ollama)
    embed_client = get_embed_client()
    if embed_client:
        try:
            start = time.monotonic()
            healthy = await embed_client.health_check()
            ms = int((time.monotonic() - start) * 1000)
            results["embedding"] = {"status": "ok" if healthy else "error", "latency_ms": ms}
        except Exception as e:
            results["embedding"] = {"status": "error", "error": str(e)}
    else:
        results["embedding"] = {"status": "not_configured"}

    results["stt"] = await check_stt_health()

    try:
        results.update(await evaluate_slos())
    except Exception as e:
        results["slo"] = {"status": "error", "error": str(e)}

    results["metrics"] = {"status": "ok", "snapshot": metrics.snapshot()}

    return results
