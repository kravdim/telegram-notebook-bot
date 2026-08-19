"""Еженедельный обзор (Sunday Review) — отправка в воскресенье в 21:00."""

import logging
from html import escape

import pendulum
from aiogram import Bot

from bot.db.crud.chronometry import get_week_stats
from bot.db.crud.memoir import get_memoir_entries, get_value_stats
from bot.db.crud.projects import get_user_projects, get_project_progress
from bot.db.crud.tasks import get_completed_in_range, get_frogs_in_range
from bot.db.crud.users import claim_date_marker, get_all_users, release_date_marker
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

# Emoji для ценностей мемуарника
_VALUE_EMOJI = {
    "семья": "👨‍👩‍👧‍👦",
    "работа": "💼",
    "здоровье": "🏃",
    "дружба": "🤝",
    "развитие": "📚",
    "отдых": "🏖",
    "другое": "🔹",
}

# Категории хронометража
_CATEGORY_EMOJI = {
    "work": "💼", "personal": "👤", "rest": "☕",
    "waste": "🕳", "focus": "🎯", "unknown": "❔",
}
_CATEGORY_RU = {
    "work": "Работа", "personal": "Личное", "rest": "Отдых",
    "waste": "Хронофаги", "focus": "Фокус", "unknown": "Не разобрано",
}


async def send_weekly_review(bot: Bot) -> None:
    """Проверить и отправить еженедельный обзор (воскресенье 21:00)."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        if not user.digest_enabled:
            continue

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)

            # Только воскресенье
            if now.day_of_week != pendulum.SUNDAY:
                continue

            # После 21:00 догоняем обзор до конца воскресенья.
            target = now.set(hour=21, minute=0, second=0)
            if now < target:
                continue

            if user.weekly_review_sent_date == now.date():
                continue

            # Write-ahead marker с откатом при ошибке защищает от дублей после
            # рестарта и допускает повторную попытку при ошибке Telegram.
            async with async_session() as session:
                claimed = await claim_date_marker(
                    session, user.telegram_id, "weekly_review_sent_date", now.date()
                )
            if not claimed:
                continue
            try:
                await _send_review(bot, user, tz)
            except Exception:
                async with async_session() as session:
                    await release_date_marker(
                        session, user.telegram_id, "weekly_review_sent_date", now.date()
                    )
                raise

        except Exception as e:
            logger.error(
                "Ошибка weekly review для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


async def _send_review(bot: Bot, user, tz: str) -> None:
    """Сформировать и отправить еженедельный обзор."""
    now = pendulum.now(tz)
    week_start = now.start_of("week")
    week_end = now.end_of("week")

    async with async_session() as session:
        # 1. Хронометраж за неделю
        chrono_stats = await get_week_stats(session, user.telegram_id, tz)

        # 2. Выполненные задачи за неделю
        completed = await get_completed_in_range(
            session, user.telegram_id,
            week_start.in_tz("UTC"), week_end.in_tz("UTC"),
        )

        # 3. Лягушки за неделю
        frogs = await get_frogs_in_range(
            session, user.telegram_id,
            week_start.in_tz("UTC"), week_end.in_tz("UTC"),
        )
        frogs_eaten = [f for f in frogs if f.status == "done"]

        # 4. Ценности из мемуарника
        value_stats = await get_value_stats(session, user.telegram_id, days=7)

        # 5. Прогресс по слонам
        projects = await get_user_projects(session, user.telegram_id)
        project_progress = {}
        for p in projects[:5]:
            progress = await get_project_progress(session, p.id)
            project_progress[p.title] = progress

    # Форматируем
    text = _format_review(
        week_start=week_start,
        chrono_stats=chrono_stats,
        completed_tasks=completed,
        frogs_total=len(frogs),
        frogs_eaten=len(frogs_eaten),
        value_stats=value_stats,
        project_progress=project_progress,
    )

    await bot.send_message(chat_id=user.telegram_id, text=text, parse_mode="HTML")
    logger.info("Weekly review отправлен: %s", user.telegram_id)


def _format_review(
    week_start,
    chrono_stats: dict,
    completed_tasks: list,
    frogs_total: int,
    frogs_eaten: int,
    value_stats: list,
    project_progress: dict,
) -> str:
    """Форматирование еженедельного обзора."""
    week_end = week_start.add(days=6)
    header = (
        f"📊 <b>Обзор недели</b> "
        f"({week_start.format('DD.MM')} — {week_end.format('DD.MM')})\n"
    )
    parts = [header]

    # --- Хронометраж ---
    cats = chrono_stats.get("categories", {})
    total_min = sum(cats.values())
    entries = chrono_stats.get("entries_count", 0)

    if entries > 0:
        parts.append("⏱ <b>Распределение времени:</b>")
        for cat in ("work", "focus", "personal", "rest", "waste", "unknown"):
            minutes = cats.get(cat, 0)
            if minutes > 0:
                emoji = _CATEGORY_EMOJI.get(cat, "")
                name = _CATEGORY_RU.get(cat, cat)
                hours = minutes // 60
                mins = minutes % 60
                time_str = f"{hours}ч {mins}м" if hours else f"{mins}м"
                pct = int(minutes / total_min * 100) if total_min else 0
                bar_len = max(1, pct // 5)
                bar = "▓" * bar_len + "░" * (20 - bar_len)
                parts.append(f"  {emoji} {name}: {bar} {time_str} ({pct}%)")

        avg_prod = chrono_stats.get("avg_productivity", 0)
        if avg_prod:
            parts.append(f"  📈 Средняя продуктивность: {avg_prod}/5")
        parts.append("")

    # --- Задачи ---
    parts.append(f"✅ <b>Выполнено задач:</b> {len(completed_tasks)}")
    if completed_tasks and len(completed_tasks) <= 15:
        for t in completed_tasks:
            parts.append(f"  • {escape(t.title)}")
    elif len(completed_tasks) > 15:
        for t in completed_tasks[:10]:
            parts.append(f"  • {escape(t.title)}")
        parts.append(f"  ... и ещё {len(completed_tasks) - 10}")
    parts.append("")

    # --- Лягушки ---
    if frogs_total > 0:
        frog_pct = int(frogs_eaten / frogs_total * 100)
        bar_len = max(1, frog_pct // 5)
        bar = "▓" * bar_len + "░" * (20 - bar_len)
        parts.append(
            f"🐸 <b>Лягушки:</b> {frogs_eaten}/{frogs_total} съедено ({frog_pct}%)"
        )
        parts.append(f"  {bar}")
        if frog_pct == 100:
            parts.append("  🏆 Все лягушки съедены! Отличная неделя!")
        elif frog_pct >= 70:
            parts.append("  👍 Хороший результат!")
        elif frog_pct < 50:
            parts.append("  💪 На следующей неделе можно лучше!")
    else:
        parts.append("🐸 Лягушек на этой неделе не было.")
    parts.append("")

    # --- Ценности из мемуарника ---
    if value_stats:
        parts.append("📔 <b>Ценности недели</b> (из мемуарника):")
        for vs in value_stats[:5]:
            emoji = _VALUE_EMOJI.get(vs["value"], "🔹")
            parts.append(f"  {emoji} {escape(str(vs['value']))}: {vs['count']} раз")
        parts.append("")

    # --- Прогресс по слонам ---
    if project_progress:
        parts.append("🐘 <b>Слоны:</b>")
        for title, progress in project_progress.items():
            pct = progress.get("percent", 0)
            done = progress.get("done", 0)
            total = progress.get("total", 0)
            bar_len = max(1, pct // 5) if pct > 0 else 0
            bar = "▓" * bar_len + "░" * (20 - bar_len) if total > 0 else "░" * 20
            parts.append(f"  {escape(title)}: {bar} {pct}% ({done}/{total})")

    # Подпись
    parts.append("\n🔄 Новая неделя — новые возможности!")

    return "\n".join(parts)
