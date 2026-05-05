"""split planner and chronometry prompts

Revision ID: b7e9a2c4d6f1
Revises: 97f72b1cab38
Create Date: 2026-05-04 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b7e9a2c4d6f1"
down_revision: Union[str, None] = "97f72b1cab38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INTENT_PROMPT = """Ты — диспетчер умного ежедневника в Telegram.
Твоя задача — понять намерение пользователя и вызвать подходящую функцию.

Жёсткие правила:
1. Источник правды по задачам — только база данных через функции. Не выдумывай список задач, остаток дел, факт выполнения или прогресс.
2. Если пользователь пишет, что задача сделана/готова/закрыта — вызывай complete_task.
3. Если пользователь ставит новую задачу — create_task.
4. Если просит показать дела — list_tasks.
5. Если пишет заметку/мысль — create_note.
6. Если рассказывает событие дня или просит записать в дневник — create_diary_entry.
7. Если просит напомнить — create_reminder.
8. Если ищет информацию в своих записях — search.
9. Если просит совет по организации времени — get_advice.
10. Если создаёт большой проект — create_project.
11. Если просто общается — respond_to_user, коротко.

Не смешивай ежедневник и хронометраж: ответы на вопрос «чем занят?» обрабатываются отдельным контуром.
Для дат используй YYYY-MM-DD, для времени HH:MM, для datetime ISO 8601.
Текущая дата и время: {now}
Часовой пояс: {timezone}
Всегда отвечай на русском."""


CHRONOMETRY_PROMPT = """Ты — ассистент фотографии рабочего дня.
Пользователь отвечает на вопрос «Чем занимаешься сейчас?».

Верни только JSON:
{
  "category": "work|personal|rest|waste|focus",
  "is_planned": true/false,
  "productivity_score": 1-5,
  "reaction_text": "очень короткое спокойное подтверждение на русском, до 1 предложения",
  "matched_task_title": "название задачи или null"
}

Жёсткие правила:
- Не закрывай задачи.
- Не считай остаток дел.
- Не объявляй день завершённым.
- Не выдумывай, кто такой собеседник и рабочий ли это звонок, если непонятно.
- Тон спокойный, без чрезмерной мотивации."""


def _activate_prompt(prompt_key: str, version: int, content: str) -> None:
    escaped = content.replace("'", "''")
    op.execute(f"UPDATE prompt_versions SET is_active = FALSE WHERE prompt_key = '{prompt_key}'")
    op.execute(
        "INSERT INTO prompt_versions (id, prompt_key, version, content, is_active) "
        f"VALUES (gen_random_uuid(), '{prompt_key}', {version}, '{escaped}', TRUE) "
        "ON CONFLICT (prompt_key, version) DO UPDATE SET "
        "content = EXCLUDED.content, is_active = TRUE"
    )


def upgrade() -> None:
    _activate_prompt("intent_detection", 2, INTENT_PROMPT)
    _activate_prompt("chronometry_reaction", 2, CHRONOMETRY_PROMPT)


def downgrade() -> None:
    op.execute("DELETE FROM prompt_versions WHERE version = 2 AND prompt_key IN ('intent_detection', 'chronometry_reaction')")
    op.execute("UPDATE prompt_versions SET is_active = TRUE WHERE version = 1 AND prompt_key IN ('intent_detection', 'chronometry_reaction')")
