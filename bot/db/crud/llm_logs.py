"""Логирование LLM-запросов."""

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import LlmLog


async def log_llm_request(
    session: AsyncSession,
    user_id: Optional[int],
    prompt_key: Optional[str],
    model: str,
    input_messages: List[Dict[str, str]],
    output_content: Optional[str] = None,
    function_call: Optional[Dict[str, Any]] = None,
    total_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> LlmLog:
    """Записать лог LLM-запроса."""
    log = LlmLog(
        user_id=user_id,
        prompt_key=prompt_key,
        model=model,
        input_messages=input_messages,
        output_content=output_content,
        function_call=function_call,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        error=error,
    )
    session.add(log)
    await session.commit()
    return log
