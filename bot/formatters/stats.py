"""Форматирование статистики: /stats frogs, productivity, values."""


def format_frog_stats(
    completed_frogs: int,
    total_frogs: int,
    current_streak: int,
) -> str:
    """Статистика лягушек."""
    pct = int(completed_frogs / total_frogs * 100) if total_frogs else 0
    parts = [
        "🐸 <b>Статистика лягушек</b>\n",
        f"Съедено: {completed_frogs}/{total_frogs} ({pct}%)",
    ]
    if current_streak > 0:
        parts.append(f"🔥 Текущая серия: {current_streak} дней подряд")
    filled = min(20, pct // 5)
    bar = "█" * filled + "░" * (20 - filled)
    parts.append(f"[{bar}]")
    return "\n".join(parts)


def format_productivity_stats(
    avg_week: float,
    avg_month: float,
    trend: str,
) -> str:
    """Статистика продуктивности."""
    trend_emoji = {"up": "📈", "down": "📉", "stable": "➡️"}.get(trend, "➡️")
    trend_label = {"up": "растёт", "down": "снижается", "stable": "стабильно"}.get(
        trend, "стабильно"
    )
    parts = [
        "📊 <b>Продуктивность</b>\n",
        f"Неделя: {avg_week}/5",
        f"Месяц: {avg_month}/5",
        f"Тренд: {trend_emoji} {trend_label}",
    ]
    return "\n".join(parts)
