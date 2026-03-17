"""Форматирование мемуарника и аналитики ценностей."""

from typing import List

from bot.db.models import MemoirEntry


_VALUE_EMOJI = {
    "семья": "👨‍👩‍👧‍👦",
    "работа": "💼",
    "здоровье": "🏃",
    "дружба": "🤝",
    "развитие": "📚",
    "отдых": "🏖",
    "другое": "🔹",
}


def format_memoir_question() -> str:
    """Вопрос мемуарника на вечер."""
    return (
        "📔 <b>Мемуарник</b>\n\n"
        "Что сегодня было самым ярким, значимым событием дня?\n"
        "Опиши коротко — одно-два предложения."
    )


def format_memoir_entries(entries: List[MemoirEntry]) -> str:
    """Форматирование последних записей мемуарника."""
    if not entries:
        return "📔 Записей в мемуарнике пока нет."

    parts = ["📔 <b>Мемуарник</b> (последние записи):\n"]
    for entry in entries:
        emoji = _VALUE_EMOJI.get(entry.value_tag, "🔹") if entry.value_tag else "🔹"
        tag = f" [{entry.value_tag}]" if entry.value_tag else ""
        parts.append(
            f"📅 {entry.event_date.strftime('%d.%m')} {emoji}{tag}\n"
            f"  {entry.content}"
        )
    return "\n".join(parts)


def format_value_stats(stats: List[dict]) -> str:
    """Форматирование статистики ценностей."""
    if not stats:
        return "Пока недостаточно данных для аналитики ценностей."

    total = sum(s["count"] for s in stats)
    parts = ["📊 <b>Твои ценности</b> (последние 90 дней):\n"]

    for s in stats:
        emoji = _VALUE_EMOJI.get(s["value"], "🔹")
        pct = int(s["count"] / total * 100)
        bar = "█" * max(1, pct // 5)
        parts.append(f"{emoji} {s['value']}: {bar} {pct}% ({s['count']})")

    return "\n".join(parts)


def format_weekly_review(entries: List[MemoirEntry]) -> str:
    """Форматирование недельного ревью."""
    if not entries:
        return "На этой неделе записей в мемуарнике не было."

    parts = ["📔 <b>Неделя в мемуарнике</b>\n"]
    values = {}
    for entry in entries:
        tag = entry.value_tag or "другое"
        values[tag] = values.get(tag, 0) + 1
        emoji = _VALUE_EMOJI.get(tag, "🔹")
        parts.append(
            f"  {entry.event_date.strftime('%d.%m')} {emoji} {entry.content[:60]}"
        )

    if values:
        parts.append("\n<b>Ценности недели:</b>")
        for v, cnt in sorted(values.items(), key=lambda x: -x[1]):
            emoji = _VALUE_EMOJI.get(v, "🔹")
            parts.append(f"  {emoji} {v}: {cnt}")

    return "\n".join(parts)
