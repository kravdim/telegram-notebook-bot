"""Always-available privacy notice and cloud-processing choice."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.db.crud.users import get_user, update_user_settings
from bot.db.engine import async_session
from bot.handlers.telegram import callback_message
from bot.privacy import (
    PRIVACY_NOTICE_VERSION,
    consent_display_state,
    privacy_keyboard,
    privacy_notice_text,
    provider_fingerprint,
)

router = Router()


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    if not message.from_user:
        return
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    enabled = consent_display_state(user)
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


@router.callback_query(F.data.startswith("privacy:"))
async def cb_privacy_choice(callback: CallbackQuery) -> None:
    fingerprint = provider_fingerprint()
    enabled = callback.data == f"privacy:enable:{fingerprint}"
    if not enabled and callback.data != "privacy:disable":
        await callback.answer("Состав получателей изменился. Подтверди новый выбор.")
        await callback_message(callback).answer(
            privacy_notice_text(), parse_mode=None, reply_markup=privacy_keyboard(),
        )
        return
    async with async_session() as session:
        user = await update_user_settings(
            session,
            callback.from_user.id,
            privacy_notice_version=PRIVACY_NOTICE_VERSION,
            privacy_provider_fingerprint=fingerprint if enabled else None,
            cloud_processing_enabled=enabled,
        )
    await callback.answer("Настройка сохранена" if user else "Сначала выполни /start")
    await callback_message(callback).edit_text(
        privacy_notice_text(enabled=enabled if user else None),
        parse_mode=None,
        reply_markup=privacy_keyboard(),
    )
