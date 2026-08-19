"""Безопасное форматирование динамического текста для Telegram HTML."""

from html import escape


def escape_dynamic(value: object) -> str:
    """Экранировать пользовательские, DB- и LLM-данные для Telegram HTML."""
    return escape(str(value or ""), quote=False)


def escape_user_text(value: object) -> str:
    """Экранировать текст, пришедший от пользователя или из STT."""
    return escape_dynamic(value)


def escape_llm_text(value: object) -> str:
    """Экранировать LLM-текст, если его необходимо включить в HTML-шаблон."""
    return escape_dynamic(value)
