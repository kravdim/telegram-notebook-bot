"""Форматирование сообщений для Telegram."""

import re
from typing import List

MAX_MESSAGE_LEN = 4096


def split_message(text: str, max_len: int = MAX_MESSAGE_LEN) -> List[str]:
    """Разбить длинное сообщение на части по границе строки.

    Гарантирует что каждая часть <= max_len символов.
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Ищем последний перенос строки в пределах лимита
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


_HTML_TOKEN_RE = re.compile(r"(<[^>]+>|&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);)")
_HTML_TAG_RE = re.compile(
    r"^<\s*(/?)\s*(b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote|tg-spoiler)\b[^>]*>$",
    re.IGNORECASE,
)


def split_html_message(  # noqa: C901 - REVIEW-20260829 legacy ratchet
    text: str, max_len: int = MAX_MESSAGE_LEN
) -> List[str]:
    """Разбить Telegram HTML, не разрывая entity и балансируя теги."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    stack: list[tuple[str, str]] = []

    def closing_tags() -> str:
        return "".join(f"</{name}>" for name, _ in reversed(stack))

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current + closing_tags())
            current = "".join(open_tag for _, open_tag in stack)

    for token in _HTML_TOKEN_RE.split(text):
        if not token:
            continue
        tag = _HTML_TAG_RE.match(token)
        if tag:
            is_close, name = bool(tag.group(1)), tag.group(2).lower()
            if len(current) + len(token) + len(closing_tags()) > max_len:
                flush()
            current += token
            if is_close:
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index][0] == name:
                        stack.pop(index)
                        break
            else:
                stack.append((name, token))
            continue

        # Entity — атомарный token; обычный текст режем с учётом закрывающих тегов.
        is_entity = token.startswith("&") and token.endswith(";")
        remaining = token
        while remaining:
            reserve = len(closing_tags())
            available = max_len - len(current) - reserve
            if len(remaining) <= available:
                current += remaining
                break
            if is_entity:
                flush()
                current += remaining
                break
            if available <= 0:
                flush()
                continue
            cut = max(1, available)
            preferred = max(remaining.rfind("\n", 0, cut + 1), remaining.rfind(" ", 0, cut + 1))
            if preferred > 0:
                cut = preferred + 1
            current += remaining[:cut]
            remaining = remaining[cut:]
            flush()

    if current:
        chunks.append(current + closing_tags())
    return chunks
