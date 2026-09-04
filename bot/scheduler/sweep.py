"""Recovery sweep shares the same durable claims as the primary sender."""

from aiogram import Bot

from bot.scheduler.reminders import send_pending_reminders


async def sweep_missed_reminders(bot: Bot) -> None:
    """Retry due work without a competing delivery implementation."""
    await send_pending_reminders(bot)
