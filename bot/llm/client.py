"""LLM-клиент: MiniMax M2.7 с опциональным fallback."""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from bot.config import settings
from bot.observability import metrics

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
        fallback_cfg = llm_cfg.get("fallback")

        # API ключи по провайдеру
        api_keys = {
            "gemini": settings.gemini_api_key,
            "minimax": settings.minimax_api_key,
            "zhipu": settings.zhipu_api_key,
            "openai": settings.openai_api_key,
        }

        self.main_client = AsyncOpenAI(
            base_url=main_cfg.get("base_url", "https://api.minimax.io/v1"),
            api_key=api_keys.get(main_cfg.get("provider", "minimax"), ""),
            timeout=main_cfg.get("timeout_sec", 15),
        )
        self.main_model = main_cfg.get("model", "MiniMax-M2.7")
        self.main_max_retries = main_cfg.get("max_retries", 2)

        self.fallback_client: Optional[AsyncOpenAI] = None
        self.fallback_model = ""
        self.fallback_max_retries = 0
        if fallback_cfg:
            self.fallback_client = AsyncOpenAI(
                base_url=fallback_cfg.get("base_url", "https://api.minimax.io/v1"),
                api_key=api_keys.get(fallback_cfg.get("provider", "minimax"), ""),
                timeout=fallback_cfg.get("timeout_sec", 15),
            )
            self.fallback_model = fallback_cfg.get("model", "MiniMax-M2.7")
            self.fallback_max_retries = fallback_cfg.get("max_retries", 1)

        self._main_healthy = True

    async def chat(
        self,
        messages: List[Dict[str, str]],
        functions: Optional[List[Dict]] = None,
        timeout: Optional[float] = None,
        prompt_key: Optional[str] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """Отправить запрос. При ошибке main — retry на fallback, если он настроен."""
        # Попытка main. Если fallback не настроен, пробуем main на каждом запросе,
        # даже после предыдущей временной ошибки.
        if self._main_healthy or not self.fallback_client:
            try:
                return await self._call(
                    self.main_client, self.main_model, messages, functions,
                    timeout, self.main_max_retries, tool_choice,
                )
            except (APIConnectionError, APIError, APITimeoutError, RateLimitError) as e:
                logger.warning("Main LLM (%s) failed: %s.", self.main_model, e)
                self._main_healthy = False

        if not self.fallback_client:
            raise LLMUnavailableError(f"Main LLM unavailable: {self.main_model}")

        # Fallback
        try:
            metrics.increment("llm.fallback")
            return await self._call(
                self.fallback_client, self.fallback_model, messages, functions,
                timeout, self.fallback_max_retries, tool_choice,
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
        tool_choice: Optional[str] = None,
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
            kwargs["tool_choice"] = tool_choice or "auto"
        if timeout:
            kwargs["timeout"] = timeout

        start = time.monotonic()
        last_error: Exception = RuntimeError("unexpected: no attempts made")

        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(**kwargs)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                metrics.observe("llm.latency_ms", float(elapsed_ms))

                choice = response.choices[0]
                content = re.sub(r"<think>.*?</think>", "", choice.message.content or "", flags=re.DOTALL).strip() if choice.message.content else choice.message.content
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
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                metrics.increment("llm.error")
                last_error = e
                if attempt < max_retries:
                    logger.info("Retry %d/%d for %s: %s", attempt + 1, max_retries, model, e)
                    continue
                raise
            except APIError as e:
                metrics.increment("llm.error")
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    if attempt < max_retries:
                        continue
                raise

        raise last_error  # type: ignore

    async def health_check(self) -> bool:
        """Выполнить реальный короткий запрос к main provider."""
        try:
            await self.main_client.chat.completions.create(
                model=self.main_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=5,
            )
            if not self._main_healthy:
                logger.info("Main LLM (%s) restored.", self.main_model)
            self._main_healthy = True
            return True
        except Exception:
            self._main_healthy = False
            return False
