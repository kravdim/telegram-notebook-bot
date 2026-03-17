"""Форматирование хронометража: фотография рабочего дня, тренды."""


_CATEGORY_EMOJI = {
    "work": "💼",
    "personal": "👤",
    "rest": "☕",
    "waste": "🕳",
    "focus": "🎯",
}

_CATEGORY_RU = {
    "work": "Работа",
    "personal": "Личное",
    "rest": "Отдых",
    "waste": "Потери",
    "focus": "Фокус",
}


def format_day_photo(stats: dict) -> str:
    """Фотография рабочего дня."""
    cats = stats.get("categories", {})
    total = stats.get("total_minutes", 0)
    avg_prod = stats.get("avg_productivity", 0)
    count = stats.get("entries_count", 0)

    if not count:
        return "⏱ Записей хронометража за сегодня нет."

    parts = [f"⏱ <b>Фотография дня</b> ({count} записей)\n"]

    for cat in ("work", "focus", "personal", "rest", "waste"):
        minutes = cats.get(cat, 0)
        if minutes > 0:
            emoji = _CATEGORY_EMOJI.get(cat, "")
            name = _CATEGORY_RU.get(cat, cat)
            hours = minutes // 60
            mins = minutes % 60
            time_str = f"{hours}ч {mins}м" if hours else f"{mins}м"
            pct = int(minutes / total * 100) if total else 0
            bar = "█" * max(1, pct // 5)
            parts.append(f"{emoji} {name}: {bar} {time_str} ({pct}%)")

    if avg_prod:
        parts.append(f"\n📈 Средняя продуктивность: {avg_prod}/5")

    return "\n".join(parts)


def format_week_summary(stats: dict) -> str:
    """Недельная сводка хронометража."""
    cats = stats.get("categories", {})
    avg_prod = stats.get("avg_productivity", 0)
    count = stats.get("entries_count", 0)

    if not count:
        return "⏱ Данных хронометража за неделю нет."

    total = sum(cats.values())
    parts = [f"⏱ <b>Неделя</b> ({count} записей)\n"]

    for cat in ("work", "focus", "personal", "rest", "waste"):
        minutes = cats.get(cat, 0)
        if minutes > 0:
            emoji = _CATEGORY_EMOJI.get(cat, "")
            name = _CATEGORY_RU.get(cat, cat)
            hours = minutes // 60
            mins = minutes % 60
            time_str = f"{hours}ч {mins}м" if hours else f"{mins}м"
            parts.append(f"{emoji} {name}: {time_str}")

    if avg_prod:
        parts.append(f"\n📈 Средняя продуктивность: {avg_prod}/5")

    return "\n".join(parts)
