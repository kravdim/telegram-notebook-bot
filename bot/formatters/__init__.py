"""Форматирование сообщений для Telegram."""

import re
from dataclasses import dataclass, field
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


@dataclass
class _HtmlChunker:
    max_len: int
    chunks: list[str] = field(default_factory=list)
    current: str = ""
    stack: list[tuple[str, str]] = field(default_factory=list)

    def closing_tags(self) -> str:
        return "".join(f"</{name}>" for name, _ in reversed(self.stack))

    def flush(self) -> None:
        if self.current:
            self.chunks.append(self.current + self.closing_tags())
            self.current = "".join(open_tag for _, open_tag in self.stack)

    def add_tag(self, token: str, is_close: bool, name: str) -> None:
        if len(self.current) + len(token) + len(self.closing_tags()) > self.max_len:
            self.flush()
        self.current += token
        if not is_close:
            self.stack.append((name, token))
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == name:
                self.stack.pop(index)
                return

    def add_text(self, token: str) -> None:
        is_entity = token.startswith("&") and token.endswith(";")
        remaining = token
        while remaining:
            available = self.max_len - len(self.current) - len(self.closing_tags())
            if len(remaining) <= available:
                self.current += remaining
                return
            if is_entity:
                self.flush()
                self.current += remaining
                return
            if available <= 0:
                self.flush()
                continue
            cut = max(1, available)
            preferred = max(
                remaining.rfind("\n", 0, cut + 1),
                remaining.rfind(" ", 0, cut + 1),
            )
            cut = preferred + 1 if preferred > 0 else cut
            self.current += remaining[:cut]
            remaining = remaining[cut:]
            self.flush()

    def finish(self) -> list[str]:
        if self.current:
            self.chunks.append(self.current + self.closing_tags())
        return self.chunks


def split_html_message(text: str, max_len: int = MAX_MESSAGE_LEN) -> List[str]:
    """Разбить Telegram HTML, не разрывая entity и балансируя теги."""
    if len(text) <= max_len:
        return [text]
    chunker = _HtmlChunker(max_len)
    for token in filter(None, _HTML_TOKEN_RE.split(text)):
        if tag := _HTML_TAG_RE.match(token):
            chunker.add_tag(token, bool(tag.group(1)), tag.group(2).lower())
        else:
            chunker.add_text(token)
    return chunker.finish()
