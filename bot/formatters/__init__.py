"""Форматирование сообщений для Telegram."""

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