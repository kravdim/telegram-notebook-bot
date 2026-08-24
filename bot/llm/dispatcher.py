"""Исполнение function calls от LLM: валидация + CRUD + ответ."""

import html
import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, Dict, Optional, Tuple, TypedDict
from uuid import UUID

import pendulum
from json_repair import repair_json
from pydantic import ValidationError

from bot.db.crud.diary import create_diary_entry
from bot.db.crud.notes import create_note
from bot.db.crud.projects import (
    complete_project as crud_complete_project,
)
from bot.db.crud.projects import (
    create_project as crud_create_project,
)
from bot.db.crud.projects import (
    get_project_tasks,
    search_projects,
)
from bot.db.crud.reminders import create_reminder, is_valid_repeat_rule, upsert_task_reminder
from bot.db.crud.tasks import (
    ConcurrentTaskUpdateError,
    count_similar_completed,
    create_task,
    get_frog,
    get_today_tasks,
    get_user_tasks,
    normalize_task_identity,
    search_tasks,
    task_title_similarity,
)
from bot.db.crud.tasks import (
    update_task as crud_update_task,
)
from bot.db.crud.trips import get_active_trip
from bot.db.engine import async_session
from bot.llm.contracts import Action
from bot.observability import metrics
from bot.services.tasks import complete_task_workflow

logger = logging.getLogger(__name__)


class _DuplicateTask(TypedDict):
    id: UUID
    title: str
    scheduled_date: date | None
    due_date: date | None
    due_time: time | None
    is_frog: bool
    priority: str
    repeat_rule: str | None


def _select_confident_task(query: str, tasks: list) -> Any | None:
    """Выбрать задачу для мутации только при однозначном хорошем совпадении."""
    normalized_query = normalize_task_identity(query)
    exact = next(
        (
            task for task in tasks
            if normalize_task_identity(task.title) == normalized_query
        ),
        None,
    )
    if exact:
        return exact
    if len(normalized_query) < 5:
        return None
    strong = [
        task for task in tasks
        if task_title_similarity(query, task.title) >= 0.6
    ]
    return strong[0] if len(strong) == 1 else None


def parse_function_call(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Парсинг function call с json_repair."""
    name = raw.get("name", "")
    args_raw = raw.get("arguments", "{}")

    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            logger.warning("Невалидный JSON от LLM, применяю json_repair")
            try:
                repaired = repair_json(args_raw)
                args = json.loads(repaired)
            except (json.JSONDecodeError, ValueError) as repair_err:
                logger.error("json_repair не смог восстановить JSON: %s | raw: %.200s", repair_err, args_raw)
                args = {}
    else:
        args = args_raw

    action = Action.model_validate({"name": name, "arguments": args})
    return action.name, action.arguments


async def dispatch(
    function_call: Dict[str, Any],
    user_id: int,
    user_timezone: str = "Europe/Moscow",
) -> str:
    """Исполнить function call и вернуть текст ответа для пользователя."""
    try:
        name, args = parse_function_call(function_call)
        if name == "create_task":
            return await _handle_create_task(user_id, args, user_timezone)
        elif name == "complete_task":
            return await _handle_complete_task(user_id, args, user_timezone)
        elif name == "create_note":
            return await _handle_create_note(user_id, args)
        elif name == "create_diary_entry":
            return await _handle_create_diary(user_id, args, user_timezone)
        elif name == "create_reminder":
            return await _handle_create_reminder(user_id, args, user_timezone)
        elif name == "list_tasks":
            return await _handle_list_tasks(user_id, args, user_timezone)
        elif name == "add_birthday":
            return await _handle_add_birthday(user_id, args, user_timezone)
        elif name == "get_advice":
            return await _handle_get_advice(user_id, args)
        elif name == "respond_to_user":
            return args.get("message", "")
        elif name == "search":
            return await _handle_search(user_id, args)
        elif name == "update_task":
            return await _handle_update_task(user_id, args, user_timezone)
        elif name == "delete_task":
            return await _handle_delete_task(user_id, args)
        elif name == "create_project":
            return await _handle_create_project(user_id, args)
        elif name == "complete_project":
            return await _handle_complete_project(user_id, args)
        else:
            logger.warning("Неизвестная функция: %s", name)
            return "Не удалось обработать команду. Попробуй переформулировать."
    except ConcurrentTaskUpdateError:
        logger.warning("Конкурентное изменение задачи: %s", function_call.get("name", "?"))
        return "Задача уже изменилась в другом запросе. Проверь актуальное состояние и повтори команду."
    except ValidationError as e:
        metrics.increment("llm.invalid_tool")
        logger.warning("LLM tool contract rejected: %s", e)
        return "Ошибка распознавания команды. Переформулируй её, пожалуйста."
    except Exception as e:
        metrics.increment("llm.tool_error")
        logger.error(
            "Ошибка при выполнении tool call %s: %s",
            function_call.get("name", "?"), e, exc_info=True,
        )
        return "Произошла ошибка при обработке. Попробуй ещё раз."


def _validate_title(title: str) -> Optional[str]:
    """Валидация заголовка."""
    if not title or not title.strip():
        return "Заголовок не может быть пустым."
    if len(title) > 500:
        return "Заголовок слишком длинный (макс. 500 символов)."
    return None


def _sanitize_title(title: str) -> str:
    """Явно удалить HTML-теги из пользовательского заголовка."""
    without_tags = re.sub(r"<[^>]*>", "", title or "")
    return " ".join(html.unescape(without_tags).split()).strip()


def _parse_date(date_str: Optional[str], tz: str) -> Optional[date]:
    """Парсинг даты через pendulum."""
    if not date_str:
        return None
    try:
        dt = pendulum.parse(date_str, tz=tz)
        if not isinstance(dt, datetime):
            return None
        return dt.date()
    except Exception:
        return None


def _parse_time(time_str: Optional[str]) -> Optional[time]:
    """Парсинг времени HH:MM."""
    if not time_str:
        return None
    try:
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return None


def _parse_datetime(dt_str: Optional[str], tz: str) -> Optional[datetime]:
    """Парсинг ISO datetime через pendulum."""
    if not dt_str:
        return None
    try:
        dt = pendulum.parse(dt_str, tz=tz)
        if not isinstance(dt, datetime):
            return None
        return dt
    except Exception:
        return None


async def _handle_create_task(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    title = _sanitize_title(args.get("title", ""))
    err = _validate_title(title)
    if err:
        return err

    scheduled_date = _parse_date(args.get("scheduled_date"), tz)
    due_date = _parse_date(args.get("due_date"), tz)
    due_time = _parse_time(args.get("due_time"))
    remind_at = _parse_datetime(args.get("remind_at"), tz)

    if args.get("scheduled_date") and scheduled_date is None:
        return "Не удалось распознать дату планирования. Уточни дату."
    if args.get("due_date") and due_date is None:
        return "Не удалось распознать дедлайн. Уточни дату."
    if args.get("due_time") and due_time is None:
        return "Не удалось распознать время. Укажи его в формате ЧЧ:ММ."
    if args.get("remind_at") and remind_at is None:
        return "Не удалось распознать время напоминания. Уточни дату и время."

    if scheduled_date and scheduled_date < pendulum.now(tz).date():
        return "Дата планирования в прошлом. Уточни дату."
    if due_date and due_date < pendulum.now(tz).date():
        return "Дата дедлайна в прошлом. Уточни дату."

    category = args.get("category", "work")
    if category not in ("work", "personal"):
        category = "work"

    priority = args.get("priority", "normal")
    if priority not in ("high", "medium", "normal"):
        priority = "normal"

    is_frog = args.get("is_frog", False)
    current_trip_id = None
    async with async_session() as session:
        current_trip = await get_active_trip(
            session, user_id, pendulum.now(tz).date()
        )
        if current_trip:
            current_trip_id = current_trip.id

    # Защита от дубликатов: если есть открытая задача с таким же названием — обновляем её
    dup_info: _DuplicateTask | None = None
    async with async_session() as session:
        existing = await search_tasks(session, user_id, title, status="open")
        for t in existing:
            if normalize_task_identity(t.title) == normalize_task_identity(title):
                dup_info = {
                    "id": t.id, "title": t.title,
                    "scheduled_date": t.scheduled_date, "due_date": t.due_date,
                    "due_time": t.due_time, "is_frog": t.is_frog, "priority": t.priority,
                    "repeat_rule": t.repeat_rule,
                }
                break

    if dup_info:
        # Обновляем существующую задачу вместо создания дубликата
        updates: dict[str, Any] = {}
        if scheduled_date and scheduled_date != dup_info["scheduled_date"]:
            updates["scheduled_date"] = scheduled_date
        if due_date and due_date != dup_info["due_date"]:
            updates["due_date"] = due_date
        if due_time and due_time != dup_info["due_time"]:
            updates["due_time"] = due_time
        if remind_at:
            updates["remind_at"] = remind_at
        if is_frog and not dup_info["is_frog"]:
            updates["is_frog"] = is_frog
        if priority != "normal" and priority != dup_info["priority"]:
            updates["priority"] = priority
        if args.get("repeat_rule") and args.get("repeat_rule") != dup_info["repeat_rule"]:
            if not is_valid_repeat_rule(args["repeat_rule"]):
                return "Не удалось распознать правило повторения. Уточни периодичность."
            updates["repeat_rule"] = args["repeat_rule"]

        if updates or remind_at:
            frog_changed = False
            async with async_session() as session:
                from bot.db.crud.tasks import update_task as crud_update
                if updates.pop("is_frog", False):
                    from bot.db.crud.tasks import set_frog
                    await set_frog(session, dup_info["id"], user_id, commit=False)
                    frog_changed = True
                if updates:
                    await crud_update(
                        session, dup_info["id"], user_id, commit=False, **updates
                    )
                if remind_at:
                    await upsert_task_reminder(
                        session,
                        user_id,
                        dup_info["id"],
                        dup_info["title"],
                        remind_at,
                        None,
                        commit=False,
                    )
                await session.commit()
            changes = []
            if "scheduled_date" in updates and scheduled_date is not None:
                changes.append(f"📅 {scheduled_date.strftime('%d.%m.%Y')}")
            if "due_date" in updates and due_date is not None:
                changes.append(f"⏳ до {due_date.strftime('%d.%m.%Y')}")
            if "due_time" in updates and due_time is not None:
                changes.append(f"⏰ {due_time.strftime('%H:%M')}")
            if remind_at:
                changes.append(f"🔔 {remind_at.strftime('%d.%m %H:%M')}")
            if frog_changed:
                changes.append("🐸 лягушка")
            return f"Задача «{dup_info['title']}» уже есть — обновил: {' '.join(changes)} ✅"
        else:
            return f"Задача «{dup_info['title']}» уже существует ✅"

    repeat_rule = args.get("repeat_rule")
    if not is_valid_repeat_rule(repeat_rule):
        return "Не удалось распознать правило повторения. Уточни периодичность."
    if repeat_rule and not scheduled_date and not due_date:
        scheduled_date = pendulum.now(tz).date()

    async with async_session() as session:
        # Task и связанное напоминание фиксируются одной транзакцией. Scheduler
        # читает таблицу reminders, поэтому одного Task.remind_at недостаточно.
        if is_frog:
            current_frog = await get_frog(session, user_id)
            if current_frog:
                current_frog.is_frog = False
        task = await create_task(
            session,
            user_id=user_id,
            title=title,
            category=category,
            priority=priority,
            is_frog=is_frog,
            scheduled_date=scheduled_date,
            due_date=due_date,
            due_time=due_time,
            remind_at=remind_at,
            remind_before_min=args.get("remind_before_min"),
            repeat_rule=repeat_rule,
            trip_id=current_trip_id,
            commit=False,
        )
        if remind_at:
            await create_reminder(
                session,
                user_id,
                message=title,
                remind_at=remind_at,
                repeat_rule=None,
                task_id=task.id,
                commit=False,
            )
        await session.commit()
        await session.refresh(task)

        # Проверяем, повторяющаяся ли это задача
        similar_count, last_at = await count_similar_completed(session, user_id, title)

    parts = [f"Задача создана: {task.title}"]
    if scheduled_date:
        parts.append(f"📅 {scheduled_date.strftime('%d.%m.%Y')}")
    if due_date:
        parts.append(f"⏳ до {due_date.strftime('%d.%m.%Y')}")
    if due_time:
        parts.append(f"⏰ {due_time.strftime('%H:%M')}")
    if remind_at:
        parts.append(f"🔔 Напомню: {remind_at.strftime('%d.%m %H:%M')}")
    if is_frog:
        parts.append("🐸 Лягушка!")
    if repeat_rule:
        parts.append(f"🔄 {_format_repeat_rule(repeat_rule)}")

    result = " ".join(parts) + " ✅"

    # Комментарий для повторяющихся задач
    if similar_count >= 2:
        result += "\n" + _recurring_create_comment(title, similar_count, last_at, tz)

    return result


async def _handle_complete_task(user_id: int, args: Dict[str, Any], tz: str) -> str:
    query = args.get("search_query", "").strip()
    if not query:
        return "Уточни название задачи, которую нужно выполнить."
    async with async_session() as session:
        open_matches = await search_tasks(session, user_id, query, status="open")
        if not open_matches:
            all_matches = await search_tasks(session, user_id, query)
            done_match = next((t for t in all_matches if t.status == "done"), None)
            if done_match:
                return (
                    f"Задача «{done_match.title}» уже была выполнена.\n"
                    + await _format_today_planner_state(user_id, tz)
                )
            return "Не нашёл такую задачу. Уточни название."

        confident = _select_confident_task(query, open_matches)
        if confident is None:
            lines = [f"Нашёл несколько задач по «{query}». Уточни название:"]
            for candidate in open_matches[:5]:
                lines.append(f"  • {candidate.title}")
            return "\n".join(lines)

        completion = await complete_task_workflow(
            session, confident.id, user_id, tz
        )
        task = completion.task
        if not task:
            return "Не нашёл такую задачу. Уточни название."
        if not completion.completed:
            return (
                f"Задача «{task.title}» уже была выполнена.\n"
                + await _format_today_planner_state(user_id, tz)
            )

        task_title = task.title

        # Считаем сколько раз подобное уже выполнялось (включая текущий)
        similar_count, _ = await count_similar_completed(session, user_id, task_title)

    result = f"Задача «{task_title}» выполнена! 🎉"

    if completion.next_date:
        result += f"\n🔄 Следующая: {completion.next_date.strftime('%d.%m')}"
    elif similar_count >= 2:
        result += "\n" + _recurring_complete_comment(task_title, similar_count)

    result += "\n" + await _format_today_planner_state(user_id, tz)
    return result


async def _format_today_planner_state(user_id: int, tz: str = "Europe/Moscow") -> str:
    """Короткий детерминированный статус ежедневника после изменения задач."""
    today = pendulum.now(tz).date()
    async with async_session() as session:
        open_today = await get_today_tasks(session, user_id, today)
    return _format_open_today_state(open_today)


def _format_open_today_state(open_today) -> str:
    """Форматировать остаток задач на сегодня без обращения к БД."""
    if not open_today:
        return "На сегодня открытых задач не осталось."

    lines = [f"Осталось на сегодня: {len(open_today)}"]
    for task in open_today[:5]:
        icon = "🐸" if task.is_frog else ("🔴" if task.priority == "high" else "📌")
        time_str = f" {task.due_time.strftime('%H:%M')}" if task.due_time else ""
        lines.append(f"{icon} {task.title}{time_str}")
    if len(open_today) > 5:
        lines.append(f"... и ещё {len(open_today) - 5}")
    return "\n".join(lines)


def _format_repeat_rule(rule: str) -> str:
    """Человекочитаемый формат repeat_rule."""
    _day_names = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}

    if rule == "daily":
        return "Каждый день"
    if rule == "weekdays":
        return "Каждый будний день"
    if rule.startswith("weekly:"):
        days = rule.split(":", 1)[1].split(",")
        names = [_day_names.get(int(d), d) for d in days]
        return f"Каждый {', '.join(names)}"
    if rule.startswith("monthly:"):
        day = rule.split(":", 1)[1]
        return f"Каждый месяц {day}-го"
    if rule.startswith("every:"):
        interval = rule.split(":", 1)[1]
        num = interval[:-1]
        unit = {"d": "дн.", "w": "нед.", "m": "мес."}.get(interval[-1], "")
        return f"Каждые {num} {unit}"
    return rule


def _recurring_create_comment(title: str, count: int, last_at, tz: str) -> str:
    """Комментарий при создании повторяющейся задачи."""
    import random

    if last_at:
        last_local = pendulum.instance(last_at).in_tz(tz)
        days_ago = (pendulum.now(tz) - last_local).in_days()
    else:
        days_ago = None

    comments = [
        f"🔄 Знакомая задача! Ты уже делал подобное {count} раз.",
        f"🔄 О, снова! Это уже {count}-й раз — настоящая рутина 💪",
        f"🔄 Похоже, это регулярное дело (выполнено {count} раз). Ты профи!",
        f"🔄 Уже {count}-й раз! Может, стоит сделать повторяющуюся задачу? 😉",
    ]

    if count >= 10:
        comments.extend([
            f"🔄 {count} раз! Это уже традиция 🏆",
            f"🔄 Ветеран! {count}-е выполнение этой задачи.",
        ])

    if days_ago is not None and days_ago <= 1:
        comments.append("🔄 Только вчера делал — и снова! Вот это темп 🚀")

    result = random.choice(comments)

    if days_ago is not None and days_ago > 0:
        result += f"\n📊 Последний раз: {days_ago} дн. назад"

    return result


def _recurring_complete_comment(title: str, count: int) -> str:
    """Комментарий при завершении повторяющейся задачи."""
    import random

    if count <= 3:
        comments = [
            f"📊 Это уже {count}-й раз! Начинается традиция.",
            f"📊 {count}-е выполнение. Входишь в ритм!",
        ]
    elif count <= 10:
        comments = [
            f"📊 {count}-й раз! Стабильность — признак мастерства 💪",
            f"📊 Уже {count} раз — ты машина! 🤖",
            f"📊 {count}-е выполнение. Рутина? Нет, дисциплина! 💪",
        ]
    else:
        comments = [
            f"📊 {count}-й раз! Легенда! 🏆",
            f"📊 {count} выполнений — впечатляет! Настоящий профессионал.",
            f"📊 Ого, уже {count}! Это дело — часть твоей жизни 😄",
        ]

    return random.choice(comments)


async def _handle_create_note(user_id: int, args: Dict[str, Any]) -> str:
    content = args.get("content", "").strip()
    if not content:
        return "Заметка не может быть пустой."

    raw_title = args.get("title")
    title = _sanitize_title(raw_title) if raw_title else None
    tags = args.get("tags", [])

    async with async_session() as session:
        note = await create_note(session, user_id, content=content, title=title, tags=tags)

    return "Заметка сохранена ✅" + (f" ({note.title})" if note.title else "")


async def _handle_create_diary(user_id: int, args: Dict[str, Any], tz: str = "Europe/Moscow") -> str:
    content = args.get("content", "").strip()
    if not content:
        return "Запись не может быть пустой."

    async with async_session() as session:
        await create_diary_entry(session, user_id, content=content, tz=tz)

    return "Записано в дневник ✅"


def _extract_value_tag(content: str) -> str:
    """Извлечение ценности из текста по ключевым словам."""
    text = content.lower()

    family_kw = ("семья", "мама", "папа", "жена", "муж", "дети", "ребёнок", "ребенок",
                 "сын", "дочь", "родител", "бабушк", "дедушк", "брат", "сестр",
                 "родствен")
    health_kw = ("тренажер", "спорт", "зал", "тренировк", "бег", "пробежк",
                 "здоровь", "врач", "больниц", "фитнес", "йог", "лофт")
    friends_kw = ("друг", "друзь", "гости", "посидел",
                  "товарищ")
    growth_kw = ("учёба", "учеба", "курс", "книг", "читал", "изуч", "развити",
                 "настроил", "разобрал", "научил", "бот", "код", "программ")
    rest_kw = ("отдых", "кино", "фильм", "гулял", "прогулк", "набережн", "парк",
               "выходн", "поехал", "путешеств", "evoque", "машин")
    work_kw = ("работ", "офис", "проект", "клиент", "заказчик", "совещани",
               "митинг", "задач", "дедлайн", "письмо", "аванс", "денежн",
               "фулфилмент", "коммерческ", "предложени", "сайт")

    # Проверяем в порядке приоритета (личное > работа)
    for kw in family_kw:
        if kw in text:
            return "семья"
    for kw in health_kw:
        if kw in text:
            return "здоровье"
    for kw in friends_kw:
        if kw in text:
            return "дружба"
    for kw in rest_kw:
        if kw in text:
            return "отдых"
    for kw in growth_kw:
        if kw in text:
            return "развитие"
    for kw in work_kw:
        if kw in text:
            return "работа"

    return "другое"


async def _handle_create_reminder(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    message = args.get("message", "").strip()
    if not message:
        return "Текст напоминания не может быть пустым."

    remind_at = _parse_datetime(args.get("remind_at"), tz)
    if not remind_at:
        return "Не удалось распознать время напоминания. Укажи дату и время."

    if remind_at < pendulum.now(tz):
        return "Время напоминания в прошлом. Уточни."

    repeat_rule = args.get("repeat_rule")
    if not is_valid_repeat_rule(repeat_rule):
        return "Не удалось распознать правило повторения. Уточни периодичность."

    async with async_session() as session:
        await create_reminder(
            session, user_id,
            message=message,
            remind_at=remind_at,
            repeat_rule=repeat_rule,
        )

    return f"Напоминание установлено: {message}\n🔔 {remind_at.strftime('%d.%m.%Y %H:%M')}"


async def _handle_list_tasks(
    user_id: int, args: Dict[str, Any], tz: str
) -> str:
    scope = args.get("scope", "today")
    today = pendulum.now(tz).date()

    async with async_session() as session:
        if scope == "today":
            tasks = await get_today_tasks(session, user_id, today)
            if not tasks:
                return "На сегодня задач нет. Свободный день! 🎉"

            lines = ["📋 Задачи на сегодня:\n"]
            for t in tasks:
                icon = "🐸" if t.is_frog else ("🔴" if t.priority == "high" else "📌")
                time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if t.due_time else ""
                date_str = ""
                plan_date = getattr(t, "scheduled_date", None) or t.due_date
                if plan_date and plan_date < today:
                    date_str = f" ⚠️ с {plan_date.strftime('%d.%m')}"
                lines.append(f"{icon} {t.title}{time_str}{date_str}")
            return "\n".join(lines)

        elif scope == "done_today":
            from bot.db.crud.tasks import get_completed_today
            completed = await get_completed_today(session, user_id, today, tz)
            if not completed:
                return "Сегодня пока ничего не выполнено."
            lines = [f"✅ Выполнено сегодня: {len(completed)}\n"]
            for t in completed:
                lines.append(f"  • {t.title}")
            return "\n".join(lines)

        elif scope == "overdue":
            all_tasks = await get_user_tasks(session, user_id, status="open")
            overdue = []
            for task in all_tasks:
                plan_date = getattr(task, "scheduled_date", None) or task.due_date
                if plan_date is not None and plan_date < today:
                    overdue.append(task)
            if not overdue:
                return "Просроченных задач нет 👍"
            lines = ["⚠️ Просроченные задачи:\n"]
            for t in overdue:
                plan_date = getattr(t, "scheduled_date", None) or t.due_date
                if plan_date is not None:
                    lines.append(f"📌 {t.title} ({plan_date.strftime('%d.%m')})")
            return "\n".join(lines)

        else:  # all
            tasks = await get_user_tasks(session, user_id, status="open")
            if not tasks:
                return "Открытых задач нет. Всё сделано! 🎉"
            lines = ["📋 Все открытые задачи:\n"]
            for t in tasks:
                icon = "🐸" if t.is_frog else ("🔴" if t.priority == "high" else "📌")
                plan_date = getattr(t, "scheduled_date", None) or t.due_date
                date_str = f" 📅 {plan_date.strftime('%d.%m')}" if plan_date else ""
                lines.append(f"{icon} {t.title}{date_str}")
            return "\n".join(lines)


async def _handle_search(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("query", "")
    scope = args.get("scope", "all")
    results = []
    seen = set()
    marker_match = re.search(
        r"\b[А-ЯЁA-Z]\d{1,4}-[\w-]+", query, re.IGNORECASE
    )
    exact_marker = marker_match.group(0).casefold() if marker_match else None

    def append_result(rendered: str, searchable: str) -> None:
        normalized = " ".join(searchable.casefold().replace("ё", "е").split())
        if exact_marker and exact_marker not in searchable.casefold():
            return
        if normalized in seen:
            return
        seen.add(normalized)
        results.append(rendered)

    from bot.embeddings.indexer import get_embedding
    query_emb = await get_embedding(query)
    emb_str = str(query_emb) if query_emb else None

    async with async_session() as session:
        # Поиск по задачам
        if scope in ("all", "tasks"):
            tasks = await search_tasks(session, user_id, query)
            for t in tasks[:5]:
                status = "✅" if t.status == "done" else "📌"
                append_result(f"{status} {t.title}", t.title)

        # Поиск по заметкам (гибридный)
        if scope in ("all", "notes"):
            from bot.db.crud.notes import hybrid_search_notes
            rows = await hybrid_search_notes(session, user_id, query, emb_str)
            for row in rows:
                title = row.title or (row.content[:50] if hasattr(row, "content") else str(row)[:50])
                content = row.content if hasattr(row, "content") else str(row)
                append_result(f"📝 {title}", f"{title} {content}")

        # Поиск по дневнику (гибридный)
        if scope in ("all", "diary"):
            from bot.db.crud.diary import hybrid_search_diary
            rows = await hybrid_search_diary(session, user_id, query, emb_str)
            for row in rows:
                content = row.content if hasattr(row, "content") else str(row)
                append_result(f"📓 {content[:50]}...", content)

        # Поиск по мемуарнику (гибридный)
        if scope in ("all", "memoir"):
            from bot.db.crud.memoir import hybrid_search_memoir
            rows = await hybrid_search_memoir(session, user_id, query, emb_str)
            for row in rows:
                content = row.content if hasattr(row, "content") else str(row)
                append_result(f"📔 {content[:50]}...", content)

    if results:
        return f"🔍 По запросу «{query}» найдено:\n" + "\n".join(results)
    return f"По запросу «{query}» ничего не найдено."


async def _handle_update_task(user_id: int, args: Dict[str, Any], tz: str = "Europe/Moscow") -> str:
    query = args.get("search_query", "")
    updates = args.get("updates", {})

    if not query:
        return "Укажи какую задачу обновить."
    if not updates:
        return "Не указаны изменения."

    async with async_session() as session:
        tasks = await search_tasks(session, user_id, query)
        if not tasks:
            return f"Не нашёл задачу «{query}»."

        confident = _select_confident_task(query, tasks)
        if confident is None:
            top3 = tasks[:3]
            lines = [f"Несколько задач похожи на «{query}». Уточни:"]
            for i, task in enumerate(top3, 1):
                lines.append(f"  {i}. {task.title}")
            return "\n".join(lines)

        task = confident
        # Фильтруем допустимые поля
        allowed = {"title", "priority", "is_frog", "scheduled_date", "due_date", "due_time", "status"}
        clean_updates = {k: v for k, v in updates.items() if k in allowed}

        if not clean_updates:
            return "Нет допустимых полей для обновления."

        # Парсим даты/время из строк
        if "scheduled_date" in clean_updates and isinstance(clean_updates["scheduled_date"], str):
            parsed = _parse_date(clean_updates["scheduled_date"], tz)
            if parsed is None:
                return "Не удалось распознать дату планирования. Уточни дату."
            clean_updates["scheduled_date"] = parsed
        if "due_date" in clean_updates and isinstance(clean_updates["due_date"], str):
            parsed = _parse_date(clean_updates["due_date"], tz)
            if parsed is None:
                return "Не удалось распознать дедлайн. Уточни дату."
            clean_updates["due_date"] = parsed
        if "due_time" in clean_updates and isinstance(clean_updates["due_time"], str):
            parsed_time = _parse_time(clean_updates["due_time"])
            if parsed_time is None:
                return "Не удалось распознать время. Укажи его в формате ЧЧ:ММ."
            clean_updates["due_time"] = parsed_time

        if "title" in clean_updates:
            clean_updates["title"] = _sanitize_title(str(clean_updates["title"]))
            err = _validate_title(str(clean_updates["title"]))
            if err:
                return err
        if clean_updates.get("status") not in (None, "open", "done", "cancelled"):
            return "Недопустимый статус задачи."

        updated = await crud_update_task(session, task.id, user_id, **clean_updates)

    if updated:
        if clean_updates.get("status") == "cancelled":
            return f"Задача «{updated.title}» отменена ✅"
        if clean_updates.get("status") == "done":
            return f"Задача «{updated.title}» выполнена ✅"
        details = []
        if "scheduled_date" in clean_updates and updated.scheduled_date:
            details.append(f"📅 {updated.scheduled_date.strftime('%d.%m.%Y')}")
        if "due_date" in clean_updates and updated.due_date:
            details.append(f"⏳ до {updated.due_date.strftime('%d.%m.%Y')}")
        suffix = " " + " ".join(details) if details else ""
        return f"Задача «{updated.title}» обновлена{suffix} ✅"
    return "Не удалось обновить задачу."


async def _handle_delete_task(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "")
    if not query:
        return "Укажи какую задачу удалить."

    async with async_session() as session:
        tasks = await search_tasks(session, user_id, query)
        if not tasks:
            return f"Не нашёл задачу «{query}»."

        confident = _select_confident_task(query, tasks)
        if confident is None:
            top3 = tasks[:3]
            choices = [
                {"id": str(task.id), "title": task.title} for task in top3
            ]
            return "CHOOSE_DELETE:" + json.dumps(choices, ensure_ascii=False)

        task_id = confident.id
        task_title = confident.title

    # Возвращаем текст с предложением confirm — кнопки добавит message handler
    return f"CONFIRM_DELETE:{task_id}:{task_title}"


async def _handle_create_project(user_id: int, args: Dict[str, Any]) -> str:
    title = _sanitize_title(args.get("title", ""))
    err = _validate_title(title)
    if err:
        return err

    description = args.get("description", "")
    category = args.get("category", "work")
    if category not in ("work", "personal"):
        category = "work"

    async with async_session() as session:
        existing = await search_projects(session, user_id, title, status="active")
        exact = next(
            (project for project in existing if project.title.casefold() == title.casefold()),
            None,
        )
        if exact:
            return f"Слон «{exact.title}» уже существует ✅"
        project = await crud_create_project(
            session,
            user_id=user_id,
            title=title,
            description=description,
            category=category,
        )

    return f"PROJECT_CREATED:{project.id}:{project.title}"


async def _handle_complete_project(user_id: int, args: Dict[str, Any]) -> str:
    query = args.get("search_query", "").strip()
    if not query:
        return "Какой слон закрываем? Напиши название проекта."

    async with async_session() as session:
        projects = await search_projects(session, user_id, query, status="active")
        if not projects:
            all_projects = await search_projects(session, user_id, query, status=None)
            completed = next((p for p in all_projects if p.status == "done"), None)
            if completed:
                return f"Слон «{completed.title}» уже был закрыт ✅"
            return f"Не нашёл активного слона «{query}»."

        exact = next((p for p in projects if p.title.lower().strip() == query.lower()), None)
        if len(projects) > 1 and not exact:
            lines = [f"Нашёл несколько слонов по «{query}». Уточни название:"]
            for p in projects[:3]:
                lines.append(f"  • {p.title}")
            return "\n".join(lines)

        selected = exact or projects[0]
        project_tasks = await get_project_tasks(session, selected.id)
        open_count = sum(task.status == "open" for task in project_tasks)
        if open_count:
            return (
                f"CONFIRM_PROJECT_COMPLETE:{selected.id}:{selected.title}:{open_count}"
            )
        project = await crud_complete_project(session, selected.id, user_id)

    if not project:
        return f"Не удалось закрыть слона «{query}»."
    return f"Слон «{project.title}» закрыт ✅"


async def _handle_add_birthday(user_id: int, args: Dict[str, Any], tz: str) -> str:
    """Добавить день рождения."""
    name = args.get("name", "").strip()
    if not name:
        return "Укажи имя человека."

    date_str = args.get("date")
    bd = _parse_date(date_str, tz)
    if not bd:
        return "Не удалось распознать дату. Укажи в формате ДД.ММ или ДД месяц."

    note = args.get("note")

    from bot.db.crud.birthdays import add_birthday
    async with async_session() as session:
        year_known = bool(args.get("year_known", bd.year != 1900))
        await add_birthday(
            session, user_id, name=name, birth_date=bd, note=note,
            year_known=year_known,
        )

    result = f"🎂 Запомнил: {name} — {bd.strftime('%d.%m')}"
    if note:
        result += f"\n📝 {note}"
    result += "\nБуду напоминать в утреннем дайджесте!"
    return result


async def _handle_get_advice(user_id: int, args: Dict[str, Any]) -> str:
    """Поиск совета в базе знаний Архангельского."""
    query = args.get("query", "").strip()
    if not query:
        return "Задай вопрос — я подскажу по методике Архангельского."

    from bot.db.crud.knowledge import hybrid_search
    from bot.embeddings.indexer import get_embedding

    query_emb = await get_embedding(query)

    async with async_session() as session:
        chunks = await hybrid_search(
            session, query, query_embedding=query_emb, limit=3,
        )

    if not chunks:
        return (
            "Не нашёл подходящего совета в базе знаний. "
            "Попробуй переформулировать вопрос, например: "
            "«Как справиться с прокрастинацией?» или «Как декомпозировать слона?»"
        )

    # Формируем ответ из найденных чанков
    parts = ["📚 Совет по методике Архангельского:\n"]
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"{chunk.content}")
        if i < len(chunks):
            parts.append("")  # пустая строка между чанками

    parts.append(f"\n📖 Источник: {chunks[0].source}")
    return "\n".join(parts)
