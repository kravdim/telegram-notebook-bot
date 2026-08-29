"""Deterministic recognition of read-only task-list questions."""

import re
from typing import Literal

TaskListScope = Literal["today", "all", "overdue", "done_today"]

_TASK_WORD_RE = re.compile(r"\b(?:задач(?:а|и|у|е|ей|ам|ами|ах)?|дел(?:а|о|у|ом|ах)?)\b")
_LIST_SIGNAL_RE = re.compile(
    r"\b(?:какие|покажи|перечисли|список|перечень|что\s+у\s+меня|"
    r"что\s+(?:еще\s+)?(?:надо|нужно|осталось)\s+сделать)\b"
)
_IMPLICIT_TODAY_RE = re.compile(
    r"^что\s+(?:еще\s+)?(?:надо|нужно|осталось)\s+сделать(?:\s+сегодня)?$"
)
_MUTATION_SIGNAL_RE = re.compile(
    r"\b(?:создай|создать|добавь|добавить|удали|удалить|перенеси|перенести|"
    r"измени|изменить|закрой|закрыть|отметь|отметить)\b"
)
_DONE_RE = re.compile(r"\b(?:выполнен\w*|сделан\w*|завершен\w*|закрыт\w*)\b")


def extract_task_list_scope(text: str) -> TaskListScope | None:
    """Return a safe list scope for explicit read-only task questions."""
    normalized = " ".join(text.casefold().replace("ё", "е").strip().split())
    normalized = normalized.strip(".!?,;: ")
    if not normalized or _MUTATION_SIGNAL_RE.search(normalized):
        return None

    has_task_word = bool(_TASK_WORD_RE.search(normalized))
    if "просроч" in normalized and has_task_word:
        return "overdue"
    if _DONE_RE.search(normalized) and "сегодня" in normalized and (
        has_task_word or normalized.startswith("что ")
    ):
        return "done_today"

    if not _LIST_SIGNAL_RE.search(normalized):
        return None
    if not has_task_word and not _IMPLICIT_TODAY_RE.fullmatch(normalized):
        return None
    if re.search(r"\b(?:все|открыт\w*|вообще)\b", normalized):
        return "all"
    return "today"
