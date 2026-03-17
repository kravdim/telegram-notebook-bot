"""Обработчик свободного текста → LLM → function call."""

import logging
from typing import Optional

from aiogram import Router
from aiogram.types import Message

from bot.db.crud.users import get_user
from bot.db.engine import async_session
from bot.llm.client import LLMClient, LLMUnavailableError
from bot.llm.context import add_message, get_history, needs_compression, compress_history
from bot.llm.dispatcher import dispatch
from bot.llm.functions import FUNCTIONS
from bot.llm.prompts import get_prompt
from bot.llm.queue import LLMQueue, PRIORITY_INTENT

logger = logging.getLogger(__name__)

router = Router()

# Глобальные экземпляры — инициализируются в main.py
llm_client: Optional[LLMClient] = None
llm_queue: Optional[LLMQueue] = None


def init(client: LLMClient, queue: LLMQueue) -> None:
    """Установить ссылки на LLM-клиент и очередь."""
    global llm_client, llm_queue
    llm_client = client
    llm_queue = queue


@router.message()
async def handle_text(message: Message) -> None:
    """Свободный текст → LLM → function call / ответ."""
    if not message.text or not message.from_user:
        return

    if not llm_client or not llm_queue:
        await message.answer(
            "LLM-клиент не инициализирован. Обратитесь к администратору."
        )
        return

    user_id = message.from_user.id
    text = message.text.strip()

    # Typing indicator
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Получаем данные пользователя для timezone
    async with async_session() as session:
        user = await get_user(session, user_id)
        user_tz = user.timezone if user else "Europe/Moscow"

        # Загружаем промпт
        system_prompt = await get_prompt(session, "intent_detection")

    if not system_prompt:
        system_prompt = _default_intent_prompt()

    # Подставляем контекстные переменные в промпт
    import pendulum
    now_str = pendulum.now(user_tz).format("YYYY-MM-DD HH:mm dddd", locale="ru")
    system_prompt = system_prompt.replace("{now}", now_str).replace("{timezone}", user_tz)

    # Добавляем в историю
    add_message(user_id, "user", text)

    # Собираем сообщения
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(get_history(user_id))

    # Отправляем в LLM через очередь
    try:
        response = await llm_queue.submit(
            PRIORITY_INTENT,
            llm_client.chat(messages=messages, functions=FUNCTIONS),
        )
    except LLMUnavailableError:
        await message.answer(
            "Извини, AI-сервис временно недоступен. "
            "Попробуй через пару минут или используй команды напрямую."
        )
        return
    except Exception as e:
        logger.error("Ошибка LLM: %s", e, exc_info=True)
        await message.answer("Произошла ошибка при обработке. Попробуй ещё раз.")
        return

    # Логирование в БД
    async with async_session() as session:
        from bot.db.crud.llm_logs import log_llm_request
        await log_llm_request(
            session,
            user_id=user_id,
            prompt_key="intent_detection",
            model=response.model,
            input_messages=messages,
            output_content=response.content,
            function_call=response.function_call,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
        )

    # Обработка ответа
    if response.function_call:
        result = await dispatch(response.function_call, user_id, user_tz)

        # Специальный случай: confirm удаления
        if result.startswith("CONFIRM_DELETE:"):
            parts = result.split(":", 2)
            task_id = parts[1]
            task_title = parts[2]
            from bot.handlers.callbacks import build_delete_confirm_keyboard
            kb = build_delete_confirm_keyboard(task_id)
            await message.answer(
                f"Нашёл задачу «{task_title}». Удалить?",
                reply_markup=kb.as_markup(),
            )
            return

        # Специальный случай: проект создан → декомпозиция
        if result.startswith("PROJECT_CREATED:"):
            parts = result.split(":", 2)
            project_id = parts[1]
            project_title = parts[2]
            await message.answer(
                f"🐘 Проект «{project_title}» создан!\n"
                "Сейчас декомпозирую на задачи..."
            )
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

            from bot.llm.decompose import decompose_project, create_project_tasks
            task_titles = await decompose_project(
                llm_client, llm_queue, user_id, project_id, project_title,
            )
            if task_titles:
                created = await create_project_tasks(user_id, project_id, task_titles)
                tasks_list = "\n".join(f"  • {t}" for t in task_titles)
                add_message(user_id, "assistant", f"Проект создан с {created} задачами")
                await message.answer(
                    f"Создано {created} задач:\n{tasks_list}\n\n"
                    "Смотри /projects для прогресса."
                )
            else:
                add_message(user_id, "assistant", "Проект создан")
                await message.answer(
                    "Не удалось автоматически декомпозировать. "
                    "Добавь задачи вручную."
                )
            return

        add_message(user_id, "assistant", result)
        await message.answer(result)
    elif response.content:
        add_message(user_id, "assistant", response.content)
        await message.answer(response.content)
    else:
        await message.answer("Не удалось обработать сообщение. Попробуй переформулировать.")

    # Компрессия при необходимости
    if needs_compression(user_id):
        await _compress(user_id)


async def _compress(user_id: int) -> None:
    """Компрессия истории через LLM."""
    if not llm_client:
        return

    history = get_history(user_id)
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history)

    async with async_session() as session:
        prompt = await get_prompt(session, "context_compression")

    if not prompt:
        prompt = "Сжато перескажи этот диалог, сохранив ключевые факты и задачи:"

    try:
        response = await llm_client.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": history_text},
            ],
            timeout=20,
        )
        if response.content:
            compress_history(user_id, response.content)
    except Exception as e:
        logger.warning("Не удалось сжать контекст: %s", e)


def _default_intent_prompt() -> str:
    """Промпт по умолчанию, если БД пуста."""
    return (
        "Ты — персональный ассистент по управлению временем. "
        "Пользователь пишет тебе свободным текстом. Определи его намерение и вызови "
        "подходящую функцию: create_task, complete_task, create_note, create_diary_entry, "
        "create_reminder, search, create_project или respond_to_user.\n\n"
        "Текущая дата и время: {now}\n"
        "Часовой пояс: {timezone}\n\n"
        "Если пользователь просто общается — используй respond_to_user.\n"
        "Всегда отвечай на русском языке. Будь дружелюбным и поддерживающим."
    )
