"""Обработчик ответов хронометража и inline-кнопок."""

import json
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.db.crud.chronometry import create_time_entry, get_day_stats
from bot.db.crud.users import get_user, update_user_settings
from bot.db.engine import async_session
from bot.formatters.chronometry import format_day_photo
from bot.llm.client import LLMClient
from bot.llm.queue import LLMQueue, PRIORITY_CHRONOMETRY

logger = logging.getLogger(__name__)

router = Router()

_llm_client: Optional[LLMClient] = None
_llm_queue: Optional[LLMQueue] = None


def init(client: LLMClient, queue: LLMQueue) -> None:
    """Установить ссылки на LLM."""
    global _llm_client, _llm_queue
    _llm_client = client
    _llm_queue = queue


async def process_chronometry_response(
    user_id: int, text: str, user_tz: str,
) -> str:
    """Обработать ответ на вопрос хронометража через LLM."""
    if not _llm_client:
        return "LLM не доступен для обработки хронометража."

    prompt = (
        "Ты анализируешь ответ пользователя на вопрос «Чем занимаешься сейчас?».\n"
        "Верни JSON:\n"
        '{"category": "work|personal|rest|waste|focus", '
        '"is_planned": true/false, '
        '"productivity_score": 1-5, '
        '"reaction_text": "короткая дружелюбная реакция на русском"}\n\n'
        f'Ответ пользователя: "{text}"'
    )

    try:
        response = await _llm_queue.submit(
            PRIORITY_CHRONOMETRY,
            _llm_client.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
            ),
        )

        if not response.content:
            return "Записал ✅"

        # Парсим JSON ответ
        from json_repair import repair_json
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(repair_json(content))

        category = data.get("category", "work")
        if category not in ("work", "personal", "rest", "waste", "focus"):
            category = "work"

        async with async_session() as session:
            await create_time_entry(
                session,
                user_id=user_id,
                activity_text=text,
                category=category,
                is_planned=data.get("is_planned", False),
                productivity_score=data.get("productivity_score"),
                bot_reaction=data.get("reaction_text", ""),
            )

        reaction = data.get("reaction_text", "Записал!")
        return f"⏱ {reaction}"

    except Exception as e:
        logger.error("Ошибка обработки хронометража: %s", e)
        # Даже при ошибке LLM — записываем
        async with async_session() as session:
            await create_time_entry(
                session,
                user_id=user_id,
                activity_text=text,
                category="work",
            )
        return "Записал ✅"
