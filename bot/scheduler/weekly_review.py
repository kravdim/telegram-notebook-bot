"""Еженедельный обзор (Sunday Review) — отправка в воскресенье в 21:00."""

import logging
from html import escape

import pendulum
from aiogram import Bot

from bot.db.crud.chronometry import get_week_stats
from bot.db.crud.memoir import get_value_stats
from bot.db.crud.projects import get_project_progress, get_user_projects
from bot.db.crud.tasks import get_completed_in_range, get_frogs_in_range
from bot.db.crud.users import claim_date_marker, get_all_users
from bot.db.engine import async_session
from bot.formatters import split_html_message
from bot.logging_safety import error_type
from bot.services.delivery import DeliveryPartSpec, deliver_batch

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


async def send_weekly_review_now(bot: Bot, user) -> bool:
    """Атомарно отправить ручной weekly review для локальной даты пользователя."""
    tz = user.timezone or "Europe/Moscow"
    today = pendulum.now(tz).date()
    if getattr(user, "weekly_review_sent_date", None) == today:
        return False
    if not await _send_review(bot, user, tz):
        return False
    async with async_session() as session:
        await claim_date_marker(session, user.telegram_id, "weekly_review_sent_date", today)
    return True


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

            await send_weekly_review_now(bot, user)

        except Exception as e:
            logger.error("Ошибка weekly review: error_type=%s", error_type(e))


async def _send_review(bot: Bot, user, tz: str) -> bool:
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

    result = await deliver_batch(
        bot, delivery_key=f"weekly:{user.telegram_id}:{now.date().isoformat()}",
        user_id=user.telegram_id, kind="weekly_review",
        parts=[DeliveryPartSpec(user.telegram_id, part, parse_mode="HTML")
               for part in split_html_message(text)],
    )
    return result.completed


def _append_time_review(parts: list[str], chrono_stats: dict) -> None:
    categories = chrono_stats.get("categories", {})
    total_minutes = sum(categories.values())
    if chrono_stats.get("entries_count", 0) <= 0:
        return
    parts.append("⏱ <b>Распределение времени:</b>")
    for category in ("work", "focus", "personal", "rest", "waste", "unknown"):
        minutes = categories.get(category, 0)
        if minutes <= 0:
            continue
        percent = int(minutes / total_minutes * 100) if total_minutes else 0
        bar_length = max(1, percent // 5)
        bar = "▓" * bar_length + "░" * (20 - bar_length)
        hours, mins = divmod(minutes, 60)
        time_text = f"{hours}ч {mins}м" if hours else f"{mins}м"
        parts.append(
            f"  {_CATEGORY_EMOJI.get(category, '')} "
            f"{_CATEGORY_RU.get(category, category)}: {bar} {time_text} ({percent}%)"
        )
    if average := chrono_stats.get("avg_productivity", 0):
        parts.append(f"  📈 Средняя продуктивность: {average}/5")
    parts.append("")


def _append_completed_tasks(parts: list[str], completed_tasks: list) -> None:
    parts.append(f"✅ <b>Выполнено задач:</b> {len(completed_tasks)}")
    shown = completed_tasks if len(completed_tasks) <= 15 else completed_tasks[:10]
    parts.extend(f"  • {escape(task.title)}" for task in shown)
    if len(completed_tasks) > 15:
        parts.append(f"  ... и ещё {len(completed_tasks) - 10}")
    parts.append("")


def _append_frog_review(parts: list[str], frogs_total: int, frogs_eaten: int) -> None:
    if frogs_total <= 0:
        parts.extend(("🐸 Лягушек на этой неделе не было.", ""))
        return
    percent = int(frogs_eaten / frogs_total * 100)
    bar_length = max(1, percent // 5)
    parts.append(
        f"🐸 <b>Лягушки:</b> {frogs_eaten}/{frogs_total} съедено ({percent}%)"
    )
    parts.append(f"  {'▓' * bar_length + '░' * (20 - bar_length)}")
    if percent == 100:
        parts.append("  🏆 Все лягушки съедены! Отличная неделя!")
    elif percent >= 70:
        parts.append("  👍 Хороший результат!")
    elif percent < 50:
        parts.append("  💪 На следующей неделе можно лучше!")
    parts.append("")


def _append_values_review(parts: list[str], value_stats: list) -> None:
    if not value_stats:
        return
    parts.append("📔 <b>Ценности недели</b> (из мемуарника):")
    for value in value_stats[:5]:
        emoji = _VALUE_EMOJI.get(value["value"], "🔹")
        parts.append(f"  {emoji} {escape(str(value['value']))}: {value['count']} раз")
    parts.append("")


def _append_projects_review(parts: list[str], project_progress: dict) -> None:
    if not project_progress:
        return
    parts.append("🐘 <b>Слоны:</b>")
    for title, progress in project_progress.items():
        percent = progress.get("percent", 0)
        done, total = progress.get("done", 0), progress.get("total", 0)
        bar_length = max(1, percent // 5) if percent > 0 else 0
        bar = "▓" * bar_length + "░" * (20 - bar_length) if total > 0 else "░" * 20
        parts.append(f"  {escape(title)}: {bar} {percent}% ({done}/{total})")


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

    _append_time_review(parts, chrono_stats)
    _append_completed_tasks(parts, completed_tasks)
    _append_frog_review(parts, frogs_total, frogs_eaten)
    _append_values_review(parts, value_stats)
    _append_projects_review(parts, project_progress)
    parts.append("\n🔄 Новая неделя — новые возможности!")

    return "\n".join(parts)
