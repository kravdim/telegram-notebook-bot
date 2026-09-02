"""Исполнение function calls от LLM: валидация + CRUD + ответ."""

import html
import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import pendulum
from json_repair import repair_json
from pydantic import ValidationError

from bot.application.command_bus import CommandBus, CommandContext, CommandResult
from bot.application.intents import ApplicationIntent, intent_from_parts
from bot.application.task_creation import CreateTaskDependencies, execute_create_task
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
    set_frog,
    task_title_similarity,
)
from bot.db.crud.tasks import (
    update_task as crud_update_task,
)
from bot.db.crud.trips import get_active_trip
from bot.db.engine import async_session
from bot.llm.contracts import Action, ToolName
from bot.logging_safety import error_type, field_names, payload_size, validation_codes
from bot.observability import metrics
from bot.services.tasks import complete_task_workflow

logger = logging.getLogger(__name__)

_OPAQUE_TASK_MARKER_RE = re.compile(
    r"\b(?:DP-\d{8}T\d{6}-[a-f0-9]{6}-[\w-]+|[А-ЯЁA-Z]\d{1,4}-[\w-]+)",
    re.IGNORECASE,
)


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
    opaque_marker = _OPAQUE_TASK_MARKER_RE.search(query)
    if opaque_marker:
        marker = opaque_marker.group(0).casefold()
        marker_matches = [
            task
            for task in tasks
            if marker in normalize_task_identity(task.title).casefold()
        ]
        return marker_matches[0] if len(marker_matches) == 1 else None
    if len(normalized_query) < 5:
        return None
    strong = [
        task for task in tasks
        if task_title_similarity(query, task.title) >= 0.6
    ]
    return strong[0] if len(strong) == 1 else None


def parse_function_call(raw: Dict[str, Any]) -> Tuple[ToolName, Dict[str, Any]]:
    """Парсинг function call с json_repair."""
    name = raw.get("name", "")
    args_raw = raw.get("arguments", "{}")

    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid LLM tool JSON; attempting repair: tool=%s payload_bytes=%d",
                str(name)[:80],
                payload_size(args_raw),
            )
            try:
                repaired = repair_json(args_raw)
                args = json.loads(repaired)
            except (json.JSONDecodeError, ValueError) as repair_err:
                logger.error(
                    "LLM tool JSON repair failed: tool=%s payload_bytes=%d error_type=%s",
                    str(name)[:80],
                    payload_size(args_raw),
                    error_type(repair_err),
                )
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
    return (await dispatch_result(function_call, user_id, user_timezone)).text


async def dispatch_result(
    function_call: Dict[str, Any],
    user_id: int,
    user_timezone: str = "Europe/Moscow",
) -> CommandResult:
    """Execute a call while preserving typed application result metadata."""
    try:
        name, args = parse_function_call(function_call)
        intent = intent_from_parts(name, args)
        result = await _get_command_bus().execute(
            intent, CommandContext(user_id=user_id, timezone=user_timezone)
        )
        return result
    except ConcurrentTaskUpdateError:
        logger.warning("Конкурентное изменение задачи: %s", function_call.get("name", "?"))
        return CommandResult(
            "Задача уже изменилась в другом запросе. Проверь актуальное состояние и повтори команду.",
            "error",
        )
    except ValidationError as e:
        metrics.increment("llm.invalid_tool")
        logger.warning(
            "LLM tool contract rejected: tool=%s fields=%s errors=%s",
            str(function_call.get("name", "?"))[:80],
            field_names(function_call.get("arguments")),
            validation_codes(e),
        )
        return CommandResult(
            "Ошибка распознавания команды. Переформулируй её, пожалуйста.", "error"
        )
    except Exception as e:
        metrics.increment("llm.tool_error")
        logger.error(
            "Tool execution failed: tool=%s error_type=%s",
            str(function_call.get("name", "?"))[:80],
            error_type(e),
        )
        return CommandResult(
            "Произошла ошибка при обработке. Попробуй ещё раз.", "error"
        )


async def _execute_registered_intent(
    context: CommandContext, intent: ApplicationIntent
) -> CommandResult:
    """Adapt validated commands to the stable business handlers below."""
    args = intent.arguments()
    executor = _COMMAND_EXECUTORS[intent.name]
    text = await executor(context.user_id, args, context.timezone)
    return CommandResult.from_legacy_text(text)


async def _exec_create_task(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_create_task(user_id, args, tz)


async def _exec_complete_task(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_complete_task(user_id, args, tz)


async def _exec_create_note(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_create_note(user_id, args)


async def _exec_create_diary(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_create_diary(user_id, args, tz)


async def _exec_create_reminder(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_create_reminder(user_id, args, tz)


async def _exec_list_tasks(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_list_tasks(user_id, args, tz)


async def _exec_add_birthday(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_add_birthday(user_id, args, tz)


async def _exec_get_advice(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_get_advice(user_id, args)


async def _exec_respond(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return str(args["message"])


async def _exec_clarify(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return str(args["question"])


async def _exec_search(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_search(user_id, args)


async def _exec_update_task(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_update_task(user_id, args, tz)


async def _exec_delete_task(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_delete_task(user_id, args)


async def _exec_create_project(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_create_project(user_id, args)


async def _exec_complete_project(user_id: int, args: Dict[str, Any], tz: str) -> str:
    return await _handle_complete_project(user_id, args)


_CommandExecutor = Callable[[int, Dict[str, Any], str], Awaitable[str]]

_COMMAND_EXECUTORS: Dict[ToolName, _CommandExecutor] = {
    "create_task": _exec_create_task,
    "complete_task": _exec_complete_task,
    "create_note": _exec_create_note,
    "create_diary_entry": _exec_create_diary,
    "create_reminder": _exec_create_reminder,
    "list_tasks": _exec_list_tasks,
    "add_birthday": _exec_add_birthday,
    "get_advice": _exec_get_advice,
    "respond_to_user": _exec_respond,
    "clarify_request": _exec_clarify,
    "search": _exec_search,
    "update_task": _exec_update_task,
    "delete_task": _exec_delete_task,
    "create_project": _exec_create_project,
    "complete_project": _exec_complete_project,
}

_command_bus: CommandBus | None = None


def _get_command_bus() -> CommandBus:
    global _command_bus
    if _command_bus is None:
        bus = CommandBus()
        for command_name in _COMMAND_EXECUTORS:
            bus.register(command_name, _execute_registered_intent)
        _command_bus = bus
    return _command_bus


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
    dependencies = CreateTaskDependencies(
        session_factory=async_session,
        get_active_trip=get_active_trip,
        search_tasks=search_tasks,
        normalize_identity=normalize_task_identity,
        valid_repeat_rule=is_valid_repeat_rule,
        update_task=crud_update_task,
        set_frog=set_frog,
        upsert_task_reminder=upsert_task_reminder,
        get_frog=get_frog,
        create_task=create_task,
        create_reminder=create_reminder,
        count_similar_completed=count_similar_completed,
        sanitize_title=_sanitize_title,
        validate_title=_validate_title,
        parse_date=_parse_date,
        parse_time=_parse_time,
        parse_datetime=_parse_datetime,
        format_repeat_rule=_format_repeat_rule,
        recurring_comment=_recurring_create_comment,
    )
    return await execute_create_task(user_id, args, tz, dependencies)


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


async def _handle_list_tasks(  # noqa: C901 - REVIEW-20260829 legacy ratchet
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


async def _handle_update_task(  # noqa: C901, PLR0911, PLR0912 - REVIEW-20260829 legacy ratchet
    user_id: int, args: Dict[str, Any], tz: str = "Europe/Moscow"
) -> str:
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
            if _OPAQUE_TASK_MARKER_RE.search(query):
                return f"Не нашёл задачу «{query}»."
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
            if _OPAQUE_TASK_MARKER_RE.search(query):
                return f"Не нашёл задачу «{query}»."
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
