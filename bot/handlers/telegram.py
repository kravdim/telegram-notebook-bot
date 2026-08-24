"""Fail-closed type guards for aiogram update objects."""

from typing import cast

from aiogram import Bot
from aiogram.types import CallbackQuery, Message


def callback_message(callback: CallbackQuery) -> Message:
    """Return an editable message or reject an inaccessible callback origin."""
    message = callback.message
    if message is None or not hasattr(message, "answer") or not hasattr(message, "edit_text"):
        raise RuntimeError("Callback message is absent or inaccessible")
    return cast(Message, message)


def callback_data(callback: CallbackQuery) -> str:
    """Return callback payload after the router filter accepted the update."""
    if callback.data is None:
        raise RuntimeError("Callback data is missing")
    return callback.data


def message_bot(message: Message) -> Bot:
    """Return the bot bound by aiogram to an incoming message."""
    if message.bot is None:
        raise RuntimeError("Message is not bound to a bot")
    return message.bot
