"""LLM-клиент с fallback: DeepSeek → MiniMax."""

import logging
import time
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from bot.config import settings

logger = logging.getLogger(__name__)


class LLMResponse:
    """Результат LLM-запроса."""

    def __init__(
        self,
        content: Optional[str] = None,
        function_call: Optional[Dict[str, Any]] = None,
        function_calls: Optional[List[Dict[str, Any]]] = None,
        model: str = "",
        total_tokens: int = 0,
        latency_ms: int = 0,
    ):
        self.content = content
        self.function_call = function_call
        self.function_calls = function_calls or []
        self.model = model
        self.total_tokens = total_tokens
        self.latency_ms = latency_ms


class LLMUnavailableError(Exception):
    """Все LLM-провайдеры недоступны."""


class LLMClient:
    """Единый клиент для работы с LLM API через OpenAI SDK."""

    def __init__(self):
        yaml_cfg = settings.yaml_config
        llm_cfg = yaml_cfg.get("llm", {})
        main_cfg = llm_cfg.get("main", {})
        fallback_cfg = llm_cfg.get("fallback", {})

        # API ключи по провайдеру
        api_keys = {
            "gemini": settings.gemini_api_key,
            "deepseek": settings.deepseek_api_key,
            "minimax": settings.minimax_api_key,
            "openai": settings.openai_api_key,
        }

        self.main_client = AsyncOpenAI(
            base_url=main_cfg.get("base_url", "https://api.deepseek.com/v1"),
            api_key=api_keys.get(main_cfg.get("provider", "deepseek"), ""),
            timeout=main_cfg.get("timeout_sec", 15),
        )
        self.main_model = main_cfg.get("model", "deepseek-chat")
        self.main_max_retries = main_cfg.get("max_retries", 2)

        self.fallback_client = AsyncOpenAI(
            base_url=fallback_cfg.get("base_url", "https://api.minimax.chat/v1"),
            api_key=api_keys.get(fallback_cfg.get("provider", "minimax"), ""),
            timeout=fallback_cfg.get("timeout_sec", 15),
        )
        self.fallback_model = fallback_cfg.get("model", "MiniMax-M2.5")
        self.fallback_max_retries = fallback_cfg.get("max_retries", 1)

        self.active = "main"
        self._main_healthy = True

    async def chat(
        self,
        messages: List[Dict[str, str]],
        functions: Optional[List[Dict]] = None,
        timeout: Optional[float] = None,
        prompt_key: Optional[str] = None,
    ) -> LLMResponse:
        """Отправить запрос. При ошибке main — retry на fallback."""
        # Попытка main (если здоров)
        if self._main_healthy:
            try:
                return await self._call(
                    self.main_client, self.main_model, messages, functions,
                    timeout, self.main_max_retries,
                )
            except (APIError, APITimeoutError, RateLimitError, Exception) as e:
                logger.warning("Main LLM (%s) failed: %s. Switching to fallback.", self.main_model, e)
                self._main_healthy = False

        # Fallback
        try:
            return await self._call(
                self.fallback_client, self.fallback_model, messages, functions,
                timeout, self.fallback_max_retries,
            )
        except Exception as e:
            logger.error("Fallback LLM (%s) also failed: %s", self.fallback_model, e)
            raise LLMUnavailableError(f"All LLM providers unavailable: {e}") from e

    async def _call(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: List[Dict[str, str]],
        functions: Optional[List[Dict]],
        timeout: Optional[float],
        max_retries: int,
    ) -> LLMResponse:
        """Выполнить запрос к конкретному провайдеру."""
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if functions:
            kwargs["tools"] = [
                {"type": "function", "function": f} for f in functions
            ]
            kwargs["tool_choice"] = "auto"
        if timeout:
            kwargs["timeout"] = timeout

        start = time.monotonic()
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(**kwargs)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                choice = response.choices[0]
                content = choice.message.content
                function_call = None
                function_calls = []

                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        fc = {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                        function_calls.append(fc)
                    # Обратная совместимость: первый вызов в function_call
                    function_call = function_calls[0] if function_calls else None

                return LLMResponse(
                    content=content,
                    function_call=function_call,
                    function_calls=function_calls,
                    model=model,
                    total_tokens=response.usage.total_tokens if response.usage else 0,
                    latency_ms=elapsed_ms,
                )
            except (APITimeoutError, RateLimitError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.info("Retry %d/%d for %s: %s", attempt + 1, max_retries, model, e)
                    continue
                raise
            except APIError as e:
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    if attempt < max_retries:
                        continue
                raise

        raise last_error  # type: ignore

    async def health_check(self) -> bool:
        """Проверка доступности main провайдера.

        Если main уже здоров — не тратим запрос, просто возвращаем True.
        Проверяем только когда main помечен как нездоровый (для детекции восстановления).
        """
        if self._main_healthy:
            return True

        try:
            await self.main_client.chat.completions.create(
                model=self.main_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=5,
            )
            logger.info("Main LLM (%s) restored.", self.main_model)
            self._main_healthy = True
            return True
        except Exception:
            return False
