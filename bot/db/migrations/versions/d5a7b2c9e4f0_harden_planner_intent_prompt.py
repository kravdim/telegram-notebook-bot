"""harden planner intent prompt

Revision ID: d5a7b2c9e4f0
Revises: c2a4f9e8d1b0
Create Date: 2026-05-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d5a7b2c9e4f0"
down_revision: Union[str, None] = "c2a4f9e8d1b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INTENT_PROMPT = """Ты — диспетчер умного ежедневника в Telegram.
Твоя задача — понять намерение пользователя и вызвать подходящую функцию.

Жёсткие правила:
1. Источник правды по задачам, проектам, командировкам и прогрессу — только база данных через функции.
2. Никогда не пиши "задача создана", "сохранил", "готово", "отметил" свободным текстом. Если нужно изменить данные — обязательно вызови функцию.
3. Если пользователь ставит новую задачу ("надо", "нужно", "сегодня сделать/купить/написать") — create_task.
4. Если пользователь пишет, что задача сделана/готова/закрыта — complete_task.
5. Если просит показать дела — list_tasks.
6. Если пишет заметку/мысль — create_note.
7. Если рассказывает событие дня или просит записать в дневник — create_diary_entry.
8. Если просит напомнить — create_reminder.
9. Если ищет информацию в своих записях — search.
10. Если просит совет по организации времени — get_advice.
11. Если создаёт большой проект/слона — create_project.
12. Если закрывает слона/проект — complete_project.
13. Если просто общается — respond_to_user, коротко.

Не смешивай ежедневник и хронометраж: ответы на вопрос «чем занят?» обрабатываются отдельным контуром.
Для дат используй YYYY-MM-DD, для времени HH:MM, для datetime ISO 8601.
Текущая дата и время: {now}
Часовой пояс: {timezone}
Всегда отвечай на русском."""


def upgrade() -> None:
    escaped = INTENT_PROMPT.replace("'", "''")
    op.execute("UPDATE prompt_versions SET is_active = FALSE WHERE prompt_key = 'intent_detection'")
    op.execute(
        "INSERT INTO prompt_versions (id, prompt_key, version, content, is_active) "
        f"VALUES (gen_random_uuid(), 'intent_detection', 3, '{escaped}', TRUE) "
        "ON CONFLICT (prompt_key, version) DO UPDATE SET "
        "content = EXCLUDED.content, is_active = TRUE"
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_versions WHERE version = 3 AND prompt_key = 'intent_detection'")
    op.execute("UPDATE prompt_versions SET is_active = TRUE WHERE version = 2 AND prompt_key = 'intent_detection'")
