"""Логирование LLM-запросов."""

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import LlmLog
from bot.config import settings


def _metadata_only_function_call(call: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Оставить структуру вызова без пользовательских значений аргументов."""
    if not call:
        return None
    raw_args = call.get("arguments")
    if isinstance(raw_args, dict):
        keys = sorted(raw_args)
    else:
        keys = []
    return {"name": call.get("name"), "argument_keys": keys}


def _metadata_only_function_calls(calls: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [item for call in calls if (item := _metadata_only_function_call(call))]


async def log_llm_request(
    session: AsyncSession,
    user_id: Optional[int],
    prompt_key: Optional[str],
    model: str,
    input_messages: List[Dict[str, str]],
    output_content: Optional[str] = None,
    function_call: Optional[Dict[str, Any]] = None,
    function_calls: Optional[list[Dict[str, Any]]] = None,
    total_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> LlmLog:
    """Записать лог LLM-запроса."""
    store_payloads = bool(
        settings.yaml_config.get("privacy", {}).get("store_llm_payloads", False)
    )
    log = LlmLog(
        user_id=user_id,
        prompt_key=prompt_key,
        model=model,
        input_messages=input_messages if store_payloads else [],
        output_content=output_content if store_payloads else None,
        function_call=(
            {"calls": function_calls}
            if store_payloads and function_calls
            else function_call
            if store_payloads
            else {"calls": _metadata_only_function_calls(function_calls)}
            if function_calls
            else _metadata_only_function_call(function_call)
        ),
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        error=error[:500] if error else None,
    )
    session.add(log)
    await session.commit()
    return log
