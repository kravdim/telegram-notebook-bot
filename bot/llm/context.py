"""Bounded in-process conversation history without provider housekeeping."""

import logging
from typing import Dict, List

import tiktoken

from bot.config import settings
from bot.observability import metrics

logger = logging.getLogger(__name__)

# История: user_id → list[{"role": ..., "content": ...}]
_histories: Dict[int, List[Dict[str, str]]] = {}

_yaml = settings.yaml_config
_MAX_TOKENS = _yaml.get("context", {}).get("max_tokens", 3000)
_KEEP_RECENT_PAIRS = _yaml.get("context", {}).get("keep_recent_pairs", 5)

_enc: tiktoken.Encoding | None
try:
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None


def _count_tokens(messages: List[Dict[str, str]]) -> int:
    """Приблизительный подсчёт токенов."""
    if _enc:
        return sum(len(_enc.encode(m.get("content", ""))) for m in messages)
    # Fallback: ~4 символа на токен
    return sum(len(m.get("content", "")) // 4 for m in messages)


def get_history(user_id: int) -> List[Dict[str, str]]:
    """Return a bounded copy; provider callers never see an oversized history."""
    _enforce_budget(user_id)
    return [dict(item) for item in _histories.get(user_id, [])]


def add_message(user_id: int, role: str, content: str) -> None:
    """Add one message while preserving the global history-size invariant."""
    if user_id not in _histories:
        _histories[user_id] = []
    _histories[user_id].append({"role": role, "content": content})
    _enforce_budget(user_id)


def needs_trimming(user_id: int) -> bool:
    """Return whether history is over the configured deterministic budget."""
    history = _histories.get(user_id, [])
    return _count_tokens(history) > _MAX_TOKENS


def trim_history(user_id: int) -> None:
    """Drop old turns while preserving the most recent complete pairs.

    Context maintenance is deliberately local and deterministic: an optional
    provider call must never keep the per-user request lock after a reply was
    delivered.
    """
    _enforce_budget(user_id, force_recent_limit=True)


def _enforce_budget(user_id: int, *, force_recent_limit: bool = False) -> None:
    history = _histories.get(user_id, [])
    if not history:
        metrics.gauge("messages.context_items", 0)
        metrics.gauge("messages.context_tokens", 0)
        return

    keep_count = max(2, _KEEP_RECENT_PAIRS * 2)
    if force_recent_limit or len(history) > keep_count:
        history = history[-keep_count:]
    # Keep at least the current conversational pair intact. Real Telegram
    # inputs are size-limited before this layer; the two-message floor also
    # prevents a dangling assistant reply from becoming the whole context.
    while len(history) > 2 and _count_tokens(history) > _MAX_TOKENS:
        history.pop(0)

    _histories[user_id] = history
    metrics.gauge("messages.context_items", float(len(history)))
    metrics.gauge("messages.context_tokens", float(_count_tokens(history)))


def clear_history(user_id: int) -> None:
    """Очистить историю пользователя."""
    _histories.pop(user_id, None)


def clear_all() -> None:
    """Очистить всю историю (при рестарте)."""
    _histories.clear()
