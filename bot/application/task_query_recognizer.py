"""Deterministic recognition of read-only task-list questions."""

import re
from dataclasses import dataclass
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
_UNSUPPORTED_TEMPORAL_RE = re.compile(
    r"\b(?:завтра|послезавтра|вчера|позавчера|понедельник\w*|вторник\w*|"
    r"сред(?:а|у|ы)|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|"
    r"недел\w*|месяц\w*)\b|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b"
)
_CONTEXT_QUALIFIER_RE = re.compile(
    r"\b(?:по\s+проекту|в\s+проекте|из\s+проекта|проект\s+[а-яa-z0-9]|"
    r"командиров\w*|поездк\w*|рабоч\w*|личн\w*|персональн\w*|"
    r"приоритет\w*|срочн\w*|важн\w*|для\s+[а-яёa-z][\w-]+)\b"
)


@dataclass(frozen=True, slots=True)
class TaskListRecognition:
    """Lossless result: supported scope or named qualifiers we cannot express."""

    scope: TaskListScope | None = None
    unsupported_qualifiers: tuple[str, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return bool(self.unsupported_qualifiers)


def recognize_task_list_query(text: str) -> TaskListRecognition | None:
    """Recognize a task-list request without silently dropping constraints."""
    normalized = " ".join(text.casefold().replace("ё", "е").strip().split())
    normalized = normalized.strip(".!?,;: ")
    if not normalized or _MUTATION_SIGNAL_RE.search(normalized):
        return None

    has_task_word = bool(_TASK_WORD_RE.search(normalized))
    is_list_request = bool(_LIST_SIGNAL_RE.search(normalized)) and (
        has_task_word or bool(_IMPLICIT_TODAY_RE.fullmatch(normalized))
    )
    is_overdue_request = "просроч" in normalized and has_task_word
    is_done_request = bool(_DONE_RE.search(normalized)) and (
        has_task_word or normalized.startswith("что ")
    )
    if not (is_list_request or is_overdue_request or is_done_request):
        return None

    unsupported: list[str] = []
    if _UNSUPPORTED_TEMPORAL_RE.search(normalized):
        unsupported.append("date_or_period")
    if _CONTEXT_QUALIFIER_RE.search(normalized):
        unsupported.append("task_context")
    all_requested = bool(re.search(r"\b(?:все|открыт\w*|вообще)\b", normalized))
    if all_requested and "сегодня" in normalized:
        unsupported.append("scope_with_date")
    if unsupported:
        return TaskListRecognition(unsupported_qualifiers=tuple(dict.fromkeys(unsupported)))

    if is_overdue_request:
        return TaskListRecognition(scope="overdue")
    if is_done_request and "сегодня" in normalized:
        return TaskListRecognition(scope="done_today")
    if is_done_request:
        return TaskListRecognition(unsupported_qualifiers=("completion_period",))
    return TaskListRecognition(scope="all" if all_requested else "today")


def extract_task_list_scope(text: str) -> TaskListScope | None:
    """Return a safe list scope for explicit read-only task questions."""
    recognition = recognize_task_list_query(text)
    return recognition.scope if recognition and not recognition.needs_clarification else None
