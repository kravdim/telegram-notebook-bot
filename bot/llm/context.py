"""Управление контекстом диалога: хранение истории + компрессия."""

import logging
from typing import Dict, List, Optional

import tiktoken

from bot.config import settings

logger = logging.getLogger(__name__)

# История: user_id → list[{"role": ..., "content": ...}]
_histories: Dict[int, List[Dict[str, str]]] = {}

_yaml = settings.yaml_config
_MAX_TOKENS = _yaml.get("context", {}).get("max_tokens", 3000)
_KEEP_RECENT_PAIRS = _yaml.get("context", {}).get("keep_recent_pairs", 5)

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
    """Получить историю диалога пользователя."""
    return _histories.get(user_id, [])


def add_message(user_id: int, role: str, content: str) -> None:
    """Добавить сообщение в историю."""
    if user_id not in _histories:
        _histories[user_id] = []
    _histories[user_id].append({"role": role, "content": content})


def needs_compression(user_id: int) -> bool:
    """Проверить, нужна ли компрессия контекста."""
    history = _histories.get(user_id, [])
    return _count_tokens(history) > _MAX_TOKENS


def compress_history(user_id: int, summary: str) -> None:
    """Заменить старые сообщения на саммари, сохранив последние пары."""
    history = _histories.get(user_id, [])
    if not history:
        return

    # Сохраняем последние N пар (user + assistant)
    keep_count = _KEEP_RECENT_PAIRS * 2
    recent = history[-keep_count:] if len(history) > keep_count else history

    _histories[user_id] = [
        {"role": "system", "content": f"Краткое содержание предыдущего диалога:\n{summary}"},
        *recent,
    ]


def clear_history(user_id: int) -> None:
    """Очистить историю пользователя."""
    _histories.pop(user_id, None)


def clear_all() -> None:
    """Очистить всю историю (при рестарте)."""
    _histories.clear()
