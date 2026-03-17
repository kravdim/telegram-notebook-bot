"""FSM онбординга — 6 шагов."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.crud.tasks import create_task
from bot.db.crud.users import get_or_create_user, get_user, update_user_settings
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

router = Router()


class OnboardingStates(StatesGroup):
    """Состояния онбординга."""
    step_name = State()
    step_timezone = State()
    step_timezone_input = State()
    step_digest_times = State()
    step_digest_times_input = State()
    step_work_schedule = State()
    step_work_schedule_input = State()
    step_concepts = State()
    step_first_task = State()


# --- Шаг 1: Приветствие и имя ---

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Обработчик /start — начинает или возобновляет онбординг."""
    if not message.from_user:
        return

    async with async_session() as session:
        user = await get_user(session, message.from_user.id)

    if user and user.onboarding_completed:
        await message.answer(
            f"С возвращением, {user.username}! Чем могу помочь?\n"
            "Напиши задачу или используй /help для списка команд."
        )
        return

    # Проверяем, есть ли сохранённое состояние FSM
    current_state = await state.get_state()
    if current_state:
        await message.answer(
            "Похоже, мы не закончили настройку. Продолжим с того места, где остановились!"
        )
        return

    first_name = message.from_user.first_name or "друг"

    kb = InlineKeyboardBuilder()
    kb.button(text=f"Использовать \"{first_name}\"", callback_data=f"onb_name_use:{first_name}")
    kb.button(text="Ввести другое", callback_data="onb_name_custom")
    kb.adjust(1)

    await message.answer(
        "Привет! 👋 Я — твой персональный ассистент по управлению временем.\n"
        "Помогу планировать день, следить за задачами и находить баланс.\n\n"
        "Как тебя зовут? Или подтверди имя из профиля:",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_name)


@router.callback_query(OnboardingStates.step_name, F.data.startswith("onb_name_use:"))
async def onb_name_use(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь подтвердил имя из профиля."""
    name = callback.data.split(":", 1)[1]
    await _save_name_and_proceed(callback, state, name)


@router.callback_query(OnboardingStates.step_name, F.data == "onb_name_custom")
async def onb_name_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет ввести другое имя."""
    await callback.answer()
    await callback.message.answer("Введи своё имя:")


@router.message(OnboardingStates.step_name)
async def onb_name_text(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл имя текстом."""
    name = message.text.strip() if message.text else "друг"
    await _save_name_and_proceed_msg(message, state, name)


async def _save_name_and_proceed(callback: CallbackQuery, state: FSMContext, name: str) -> None:
    """Сохраняет имя и переходит к шагу 2."""
    await callback.answer()
    user_id = callback.from_user.id

    async with async_session() as session:
        await get_or_create_user(session, user_id, username=name)

    await state.update_data(username=name)
    await _send_timezone_step(callback.message, state, name)


async def _save_name_and_proceed_msg(message: Message, state: FSMContext, name: str) -> None:
    """Сохраняет имя и переходит к шагу 2 (из текстового ввода)."""
    user_id = message.from_user.id

    async with async_session() as session:
        await get_or_create_user(session, user_id, username=name)

    await state.update_data(username=name)
    await _send_timezone_step(message, state, name)


# --- Шаг 2: Часовой пояс ---

async def _send_timezone_step(message: Message, state: FSMContext, username: str) -> None:
    """Отправляет шаг выбора часового пояса."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, верно", callback_data="onb_tz_ok")
    kb.button(text="Изменить", callback_data="onb_tz_change")
    kb.adjust(2)

    await message.answer(
        f"Отлично, {username}! Настроим часовой пояс.\n"
        "Предполагаю Europe/Moscow — верно?",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_timezone)


@router.callback_query(OnboardingStates.step_timezone, F.data == "onb_tz_ok")
async def onb_tz_ok(callback: CallbackQuery, state: FSMContext) -> None:
    """Часовой пояс подтверждён."""
    await callback.answer()
    await state.update_data(timezone="Europe/Moscow")
    await _send_digest_step(callback.message, state)


@router.callback_query(OnboardingStates.step_timezone, F.data == "onb_tz_change")
async def onb_tz_change(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет изменить часовой пояс."""
    await callback.answer()
    await callback.message.answer(
        "Введи свой часовой пояс (например, Europe/London, Asia/Novosibirsk):"
    )
    await state.set_state(OnboardingStates.step_timezone_input)


@router.message(OnboardingStates.step_timezone_input)
async def onb_tz_text(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл часовой пояс."""
    tz = message.text.strip() if message.text else "Europe/Moscow"
    await state.update_data(timezone=tz)
    await _send_digest_step(message, state)


# --- Шаг 3: Время дайджестов ---

async def _send_digest_step(message: Message, state: FSMContext) -> None:
    """Отправляет шаг настройки времени дайджестов."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отлично", callback_data="onb_digest_ok")
    kb.button(text="Изменить время", callback_data="onb_digest_change")
    kb.adjust(2)

    await message.answer(
        "Утренний дайджест: 08:00\n"
        "Вечерний итог: 21:00\n"
        "Вопрос мемуарника: 20:45\n\n"
        "Подходит?",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_digest_times)


@router.callback_query(OnboardingStates.step_digest_times, F.data == "onb_digest_ok")
async def onb_digest_ok(callback: CallbackQuery, state: FSMContext) -> None:
    """Время дайджестов подтверждено."""
    await callback.answer()
    await state.update_data(
        digest_morning="08:00",
        digest_evening="21:00",
        memoir_prompt="20:45",
    )
    await _send_work_schedule_step(callback.message, state)


@router.callback_query(OnboardingStates.step_digest_times, F.data == "onb_digest_change")
async def onb_digest_change(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет изменить время дайджестов."""
    await callback.answer()
    await callback.message.answer(
        "Введи время в формате:\n"
        "утро=08:00 вечер=21:00 мемуарник=20:45\n\n"
        "Можно изменить только нужные, например: утро=07:30"
    )
    await state.set_state(OnboardingStates.step_digest_times_input)


@router.message(OnboardingStates.step_digest_times_input)
async def onb_digest_text(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл время дайджестов."""
    text = message.text or ""
    data = {"digest_morning": "08:00", "digest_evening": "21:00", "memoir_prompt": "20:45"}

    for part in text.split():
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in ("утро", "morning"):
                data["digest_morning"] = val
            elif key in ("вечер", "evening"):
                data["digest_evening"] = val
            elif key in ("мемуарник", "memoir"):
                data["memoir_prompt"] = val

    await state.update_data(**data)
    await _send_work_schedule_step(message, state)


# --- Шаг 4: Рабочий график ---

async def _send_work_schedule_step(message: Message, state: FSMContext) -> None:
    """Отправляет шаг настройки рабочего графика."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Да", callback_data="onb_work_ok")
    kb.button(text="Изменить", callback_data="onb_work_change")
    kb.button(text="Пропустить", callback_data="onb_work_skip")
    kb.adjust(3)

    await message.answer(
        "Для хронометража нужен рабочий график:\n"
        "Рабочие дни: Пн-Пт\n"
        "Время: 09:00 — 18:00\n\n"
        "Подходит?",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_work_schedule)


@router.callback_query(OnboardingStates.step_work_schedule, F.data == "onb_work_ok")
async def onb_work_ok(callback: CallbackQuery, state: FSMContext) -> None:
    """Рабочий график подтверждён."""
    await callback.answer()
    await state.update_data(
        work_days=[1, 2, 3, 4, 5],
        work_start="09:00",
        work_end="18:00",
    )
    await _send_concepts_step(callback.message, state)


@router.callback_query(OnboardingStates.step_work_schedule, F.data == "onb_work_skip")
async def onb_work_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь пропустил настройку графика."""
    await callback.answer()
    await _send_concepts_step(callback.message, state)


@router.callback_query(OnboardingStates.step_work_schedule, F.data == "onb_work_change")
async def onb_work_change(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет изменить рабочий график."""
    await callback.answer()
    await callback.message.answer(
        "Введи рабочий график в формате:\n"
        "дни=пн,вт,ср,чт,пт начало=09:00 конец=18:00"
    )
    await state.set_state(OnboardingStates.step_work_schedule_input)


_DAY_MAP = {
    "пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6, "вс": 7,
    "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7,
}


@router.message(OnboardingStates.step_work_schedule_input)
async def onb_work_text(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл рабочий график."""
    text = message.text or ""
    data: dict = {}

    for part in text.split():
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in ("дни", "days"):
                days = []
                for d in val.split(","):
                    d = d.strip().lower()
                    if d in _DAY_MAP:
                        days.append(_DAY_MAP[d])
                if days:
                    data["work_days"] = days
            elif key in ("начало", "start"):
                data["work_start"] = val
            elif key in ("конец", "end"):
                data["work_end"] = val

    await state.update_data(**data)
    await _send_concepts_step(message, state)


# --- Шаг 5: Знакомство с системой ---

async def _send_concepts_step(message: Message, state: FSMContext) -> None:
    """Отправляет описание основных концепций."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Понятно, поехали! 🚀", callback_data="onb_concepts_ok")

    await message.answer(
        "Вот три главных концепции, которые мы используем:\n\n"
        "🐸 Лягушка — самое неприятное дело дня. Съедаем первой — остаток дня легче.\n\n"
        "🐘 Слон — большой проект. Режем на маленькие бифштексы и едим по одному в день.\n\n"
        "📔 Мемуарник — каждый вечер я спрошу: что сегодня было самым ярким? "
        "Через месяц увидишь свои настоящие ценности.\n\n"
        "⏱ Хронометраж — в рабочее время буду спрашивать чем занят. "
        "Это помогает понять куда реально уходит время.\n\n"
        "🎯 Фокус — когда нужно сосредоточиться, скажи /focus и я не буду беспокоить.",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_concepts)


@router.callback_query(OnboardingStates.step_concepts, F.data == "onb_concepts_ok")
async def onb_concepts_ok(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь понял концепции, переход к первой задаче."""
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data="onb_task_skip")

    await callback.message.answer(
        "Хочешь создать первую задачу? Напиши что-нибудь, что нужно сделать — я сохраню.",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(OnboardingStates.step_first_task)


# --- Шаг 6: Первая задача ---

@router.callback_query(OnboardingStates.step_first_task, F.data == "onb_task_skip")
async def onb_task_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь пропустил создание задачи."""
    await callback.answer()
    await _finish_onboarding(callback.message, callback.from_user.id, state)


@router.message(OnboardingStates.step_first_task)
async def onb_first_task(message: Message, state: FSMContext) -> None:
    """Пользователь создал первую задачу."""
    title = message.text.strip() if message.text else None
    if not title:
        await message.answer("Не понял задачу. Напиши текстом или нажми «Пропустить».")
        return

    async with async_session() as session:
        task = await create_task(session, message.from_user.id, title=title)

    await message.answer(f"Задача создана: {task.title} ✅")
    await _finish_onboarding(message, message.from_user.id, state)


async def _finish_onboarding(message: Message, user_id: int, state: FSMContext) -> None:
    """Завершает онбординг: сохраняет настройки, очищает FSM."""
    data = await state.get_data()
    from datetime import time as dt_time

    settings_update: dict = {"onboarding_completed": True}

    if tz := data.get("timezone"):
        settings_update["timezone"] = tz
    if name := data.get("username"):
        settings_update["username"] = name

    # Время дайджестов
    if morning := data.get("digest_morning"):
        h, m = morning.split(":")
        settings_update["digest_morning_time"] = dt_time(int(h), int(m))
    if evening := data.get("digest_evening"):
        h, m = evening.split(":")
        settings_update["digest_evening_time"] = dt_time(int(h), int(m))
    if memoir := data.get("memoir_prompt"):
        h, m = memoir.split(":")
        settings_update["memoir_prompt_time"] = dt_time(int(h), int(m))

    # Рабочий график
    if work_days := data.get("work_days"):
        settings_update["work_days"] = work_days
    if work_start := data.get("work_start"):
        h, m = work_start.split(":")
        settings_update["work_start_time"] = dt_time(int(h), int(m))
    if work_end := data.get("work_end"):
        h, m = work_end.split(":")
        settings_update["work_end_time"] = dt_time(int(h), int(m))

    async with async_session() as session:
        await update_user_settings(session, user_id, **settings_update)

    await state.clear()

    await message.answer(
        "Настройка завершена! 🎉\n\n"
        "Теперь ты можешь:\n"
        "• Написать задачу свободным текстом\n"
        "• Отправить голосовое сообщение\n"
        "• Использовать /help для списка команд\n\n"
        "Поехали! 🚀"
    )
