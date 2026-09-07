"""Атомарное завершение профиля и создание первой задачи."""

from sqlalchemy import select

from bot.db.crud.tasks import create_task
from bot.db.crud.users import update_user_settings
from bot.db.engine import async_session
from bot.db.models import User


async def complete_onboarding(
    user_id: int, profile: dict, first_task_title: str | None = None,
) -> str | None:
    if first_task_title is not None and not 1 <= len(first_task_title.strip()) <= 500:
        raise ValueError("First task title must contain 1 to 500 characters")
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_id)
                                    .with_for_update().execution_options(populate_existing=True))
        if user is None:
            raise ValueError("Onboarding user is missing")
        if user.onboarding_completed:
            return None
        if first_task_title is not None:
            await create_task(session, user_id, title=first_task_title.strip(), commit=False)
        await update_user_settings(session, user_id, commit=False,
                                   **{**profile, "onboarding_completed": True})
        await session.commit()
    return first_task_title
