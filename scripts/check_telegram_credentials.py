#!/usr/bin/env python3
"""Verify the configured Telegram bot identity without exposing its token."""

from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from bot.config import settings


async def check() -> None:
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token":
        raise SystemExit("Telegram credential check failed: BOT_TOKEN is not configured")
    proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY")
    session = AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()
    bot = Bot(settings.bot_token, session=session)
    try:
        identity = await bot.get_me()
    finally:
        await bot.session.close()
    print(f"telegram credentials ok: @{identity.username} id={identity.id}")


if __name__ == "__main__":
    asyncio.run(check())
