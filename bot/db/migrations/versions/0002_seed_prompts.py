"""Seed initial prompts

Revision ID: 0002_seed_prompts
Revises: 2f149b9afa71
Create Date: 2026-03-17 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0002_seed_prompts'
down_revision: Union[str, None] = '2f149b9afa71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROMPTS = [
    {
        "prompt_key": "intent_detection",
        "version": 1,
        "is_active": True,
        "content": """Ты — персональный ассистент по управлению временем в Telegram.
Пользователь пишет тебе свободным текстом. Определи его намерение и вызови подходящую функцию.

Правила:
1. Если пользователь хочет создать задачу/дело → create_task
2. Если хочет отметить задачу выполненной → complete_task
3. Если хочет записать заметку/мысль → create_note
4. Если хочет записать в дневник/рассказать о дне → create_diary_entry
5. Если хочет напоминание → create_reminder (парси дату и время)
6. Если ищет что-то → search
7. Если хочет создать большой проект → create_project
8. Если просто общается/спрашивает → respond_to_user

Для дат используй формат YYYY-MM-DD, для времени HH:MM, для datetime ISO 8601.
Если пользователь говорит "завтра" — вычисли дату. "В 3" = 15:00 (если рабочее время).
Всегда отвечай на русском. Будь дружелюбным и поддерживающим.""",
    },
    {
        "prompt_key": "chronometry_reaction",
        "version": 1,
        "is_active": True,
        "content": """Ты — ассистент хронометража. Пользователь отвечает на вопрос "Чем ты сейчас занят?".

Проанализируй ответ и верни JSON:
{
  "category": "work|personal|rest|waste|focus",
  "is_planned": true/false,
  "productivity_score": 1-5,
  "reaction_text": "короткая поддерживающая реакция",
  "matched_task_title": "название задачи или null"
}

Контекст: задачи дня пользователя, время, предыдущий ответ.""",
    },
    {
        "prompt_key": "memoir_value_extraction",
        "version": 1,
        "is_active": True,
        "content": """Проанализируй текст ГСД (главное событие дня) и определи ценность.

Верни JSON:
{"value_tag": "семья|работа|здоровье|дружба|развитие|отдых|другое"}""",
    },
    {
        "prompt_key": "morning_digest",
        "version": 1,
        "is_active": True,
        "content": """Сформируй утренний дайджест на основе данных. Добавь мотивирующий контекст.
Структура: лягушка дня, задачи, напоминания, бифштекс слона (если есть).
Тон: бодрый, поддерживающий, без фальшивого позитива.""",
    },
    {
        "prompt_key": "evening_summary",
        "version": 1,
        "is_active": True,
        "content": """Суммаризируй дневниковые записи за день. Выдели ключевые события и достижения.
Тон: тёплый, рефлексивный.""",
    },
    {
        "prompt_key": "context_compression",
        "version": 1,
        "is_active": True,
        "content": """Сжато перескажи этот диалог, сохранив:
- Созданные задачи и их параметры
- Важные решения и договорённости
- Контекст, который может понадобиться для следующих сообщений

Будь максимально кратким, но не теряй фактов.""",
    },
    {
        "prompt_key": "decompose_project",
        "version": 1,
        "is_active": True,
        "content": """Помоги разбить большой проект (слон) на конкретные задачи (бифштексы).

Правила:
- Каждая задача должна быть выполнима за 1-2 часа
- Задачи в логическом порядке
- Первая задача — самая простая (для быстрого старта)
- Спроси уточняющие вопросы, если проект описан размыто""",
    },
]


def upgrade() -> None:
    for p in PROMPTS:
        content = p["content"].replace("'", "''")
        op.execute(
            f"INSERT INTO prompt_versions (id, prompt_key, version, content, is_active) "
            f"VALUES (gen_random_uuid(), '{p['prompt_key']}', {p['version']}, "
            f"'{content}', {p['is_active']})"
        )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_versions WHERE version = 1")
