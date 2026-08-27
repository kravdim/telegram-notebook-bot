"""Always-available privacy notice and cloud-processing choice."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.db.crud.users import get_user, update_user_settings
from bot.db.engine import async_session
from bot.handlers.telegram import callback_message
from bot.privacy import PRIVACY_NOTICE_VERSION, privacy_keyboard, privacy_notice_text

router = Router()


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    if not message.from_user:
        return
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    enabled = user.cloud_processing_enabled if user else None
    await message.answer(
        privacy_notice_text(enabled=enabled),
        parse_mode=None,
        reply_markup=privacy_keyboard(),
    )


@router.message(Command("delete_data"))
async def cmd_delete_data(message: Message) -> None:
    await message.answer(
        "Полное удаление включает профиль, задачи, проекты, поездки, напоминания, "
        "дневник, заметки, мемуарник, хронометраж и технические данные. Сначала "
        "скачай /export, если нужна копия. Затем отправь администратору запрос "
        "«удалить все мои данные» — оператор выполнит подтверждаемую процедуру и "
        "сообщит результат. Данные в старых backups исчезнут по сроку хранения.",
        parse_mode=None,
    )


@router.callback_query(F.data.in_({"privacy:enable", "privacy:disable"}))
async def cb_privacy_choice(callback: CallbackQuery) -> None:
    enabled = callback.data == "privacy:enable"
    async with async_session() as session:
        user = await update_user_settings(
            session,
            callback.from_user.id,
            privacy_notice_version=PRIVACY_NOTICE_VERSION,
            cloud_processing_enabled=enabled,
        )
    await callback.answer("Настройка сохранена" if user else "Сначала выполни /start")
    await callback_message(callback).edit_text(
        privacy_notice_text(enabled=enabled if user else None),
        parse_mode=None,
        reply_markup=privacy_keyboard(),
    )
