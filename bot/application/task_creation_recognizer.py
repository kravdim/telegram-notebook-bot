"""Provider- and Telegram-independent recognition of simple task creation."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pendulum

_TASK_REQUEST_PATTERNS = (
    re.compile(
        r"^\s*(?:надо|нужно|нужна|нужен|нужны|следует)\s+(?P<body>.+?)\s*[.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<date>сегодня|завтра)\s+(?:надо|нужно)\s+(?P<body>.+?)\s*[.!]*$",
        re.IGNORECASE,
    ),
)
_PRECISION_RE = re.compile(
    r"\b(?:утром|дн[её]м|вечером|ночью|полчаса|через\s+час)\b|"
    r"\b(?:в|к)\s+\d{1,2}(?::\d{2})?\b",
    re.IGNORECASE,
)
_UNSUPPORTED_DATE_RE = re.compile(
    r"\b(?:послезавтра|понедельник|вторник|среду|среда|четверг|пятницу|пятница|"
    r"субботу|суббота|воскресенье)\b|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b",
    re.IGNORECASE,
)
_QUALIFIER_PATTERNS = (
    (re.compile(r"^\s*(?P<value>сегодня|завтра)\b[\s,;:—–-]*", re.IGNORECASE), "date"),
    (re.compile(r"[\s,;:—–-]+(?P<value>сегодня|завтра)\s*$", re.IGNORECASE), "date"),
    (
        re.compile(
            r"^\s*приоритет\s+(?P<value>высок(?:ий|ого)|средн(?:ий|его)|обычный)"
            r"\b[\s,;:—–-]*",
            re.IGNORECASE,
        ),
        "priority",
    ),
    (
        re.compile(
            r"[\s,;:—–-]+приоритет\s+(?P<value>высок(?:ий|ого)|средн(?:ий|его)|обычный)"
            r"\s*$",
            re.IGNORECASE,
        ),
        "priority",
    ),
    (re.compile(r"^\s*(?P<value>срочно)\b[\s,;:—–-]*", re.IGNORECASE), "urgency"),
    (re.compile(r"[\s,;:—–-]+(?P<value>срочно)\s*$", re.IGNORECASE), "urgency"),
)


@dataclass(frozen=True)
class _TaskQualifiers:
    body: str
    date_word: str | None = None
    priority: str = "normal"


def extract_task_request(text: str, timezone: str) -> dict[str, object] | None:
    """Recognize only fields this parser can preserve without guessing."""
    stripped = text.strip()
    if _PRECISION_RE.search(stripped) or _UNSUPPORTED_DATE_RE.search(stripped):
        return None

    match = next((pattern.match(stripped) for pattern in _TASK_REQUEST_PATTERNS if pattern.match(stripped)), None)
    if match is None:
        return None

    body = match.group("body").strip(" .!?:;")
    qualifiers = _extract_qualifiers(body, match.groupdict().get("date"))
    if qualifiers is None:
        return None
    body = qualifiers.body
    date_word = qualifiers.date_word
    if not body or looks_like_chronometry_activity(body):
        return None

    title = normalize_task_title(body)
    arguments: dict[str, object] = {
        "title": title,
        "category": guess_task_category(title),
        "priority": qualifiers.priority,
    }

    today = pendulum.now(timezone).date()
    if date_word:
        arguments["scheduled_date"] = str(
            today.add(days=1) if date_word.lower() == "завтра" else today
        )
    return arguments


def _extract_qualifiers(body: str, initial_date: str | None) -> _TaskQualifiers | None:
    """Remove only exact, boundary-positioned qualifiers understood by this parser."""
    remaining = body
    date_word = initial_date
    priority = "normal"
    changed = True
    while changed:
        changed = False
        for pattern, kind in _QUALIFIER_PATTERNS:
            match = pattern.search(remaining)
            if match is None:
                continue
            value = match.group("value").casefold()
            remaining = (remaining[: match.start()] + remaining[match.end() :]).strip(" .!?:;,—–-")
            if kind == "date":
                date_word = date_word or value
            elif kind == "urgency" or value.startswith("высок"):
                priority = "high"
            elif value.startswith("средн"):
                priority = "medium"
            changed = True
            break

    # A qualifier-looking fragment that was not consumed is intentionally left
    # to the full intent path instead of being guessed or silently truncated.
    if re.search(r"\bприоритет\b", remaining, re.IGNORECASE):
        return None
    return _TaskQualifiers(body=remaining, date_word=date_word, priority=priority)


def normalize_task_title(text: str) -> str:
    """Normalize a short Russian task title."""
    title = re.sub(r"\s+", " ", text).strip(" «»\"'")
    replacements = {
        "купить": "Купить",
        "настроить": "Настроить",
        "написать": "Написать",
        "решить": "Решить",
        "записаться": "Записаться",
        "сделать": "Сделать",
        "разобраться": "Разобраться",
        "позвонить": "Позвонить",
        "отправить": "Отправить",
    }
    for source, replacement in replacements.items():
        if title.lower().startswith(source + " ") or title.lower() == source:
            return replacement + title[len(source):]
    return title[:1].upper() + title[1:]


def guess_task_category(title: str) -> str:
    """Choose the existing broad category using stable lexical signals."""
    personal_words = (
        "смесител", "ауди", "машин", "авто", "врач", "дом", "квартир",
        "купить", "магазин", "семь", "дет", "личн",
    )
    lowered = title.lower()
    return "personal" if any(word in lowered for word in personal_words) else "work"


def looks_like_chronometry_activity(text: str) -> bool:
    """Prevent ongoing activity reports from becoming tasks."""
    activity_prefixes = (
        "обедаю", "еду", "разгружаю", "настраиваю", "занимаюсь", "доделываю",
        "работаю", "пишу", "разбираюсь", "воюю", "переношу", "собираюсь",
    )
    return text.lower().startswith(activity_prefixes)
