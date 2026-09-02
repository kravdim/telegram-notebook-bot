"""Provider- and Telegram-independent recognition of simple task creation."""

from __future__ import annotations

import re

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
_TASK_LEADING_DATE_RE = re.compile(
    r"^\s*(?P<date>сегодня|завтра)\s+(?P<body>.+)$", re.IGNORECASE
)
_PRECISION_RE = re.compile(
    r"\b(?:утром|дн[её]м|вечером|ночью|полчаса|через\s+час)\b|"
    r"\b(?:в|к)\s+\d{1,2}(?::\d{2})?\b",
    re.IGNORECASE,
)


def extract_task_request(text: str, timezone: str) -> dict[str, object] | None:
    """Recognize only fields this parser can preserve without guessing."""
    stripped = text.strip()
    if _PRECISION_RE.search(stripped):
        return None

    match = next((pattern.match(stripped) for pattern in _TASK_REQUEST_PATTERNS if pattern.match(stripped)), None)
    if match is None:
        return None

    body = match.group("body").strip(" .!?:;")
    date_word = match.groupdict().get("date")
    leading_date = _TASK_LEADING_DATE_RE.match(body)
    if leading_date:
        date_word = date_word or leading_date.group("date")
        body = leading_date.group("body").strip(" .!?:;")
    if not body or looks_like_chronometry_activity(body):
        return None

    title = normalize_task_title(body)
    arguments: dict[str, object] = {
        "title": title,
        "category": guess_task_category(title),
        "priority": "normal",
    }
    lowered = stripped.lower()
    if "приоритет средн" in lowered:
        arguments["priority"] = "medium"
    elif "приоритет высок" in lowered or "срочно" in lowered:
        arguments["priority"] = "high"

    today = pendulum.now(timezone).date()
    if date_word:
        arguments["scheduled_date"] = str(
            today.add(days=1) if date_word.lower() == "завтра" else today
        )
    elif "сегодня" in lowered:
        arguments["scheduled_date"] = str(today)
    elif "завтра" in lowered:
        arguments["scheduled_date"] = str(today.add(days=1))
    return arguments


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
