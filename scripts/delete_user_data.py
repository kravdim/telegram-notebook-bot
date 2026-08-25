#!/usr/bin/env python3
"""Dry-run-first operator workflow for a user's privacy deletion request."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.config import BASE_DIR, settings
from bot.db.engine import async_session, engine
from bot.runtime.singleton import SingletonLease
from bot.services.access_config import (
    read_allowed_telegram_ids,
    remove_allowed_telegram_id,
    write_allowed_telegram_ids,
)
from bot.services.user_deletion import (
    confirmation_phrase,
    delete_user_data,
    user_data_counts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telegram_id", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", help="Required exact phrase for --execute")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "config.yaml")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, object]:
    user_id = args.telegram_id
    expected = confirmation_phrase(user_id)
    if args.execute and args.confirm != expected:
        raise ValueError(f"--execute requires --confirm {expected}")
    if args.execute and user_id in settings.admin_telegram_ids:
        raise ValueError("refusing to delete an administrator account")
    if args.execute and settings.allow_all_users:
        raise ValueError("refusing deletion while ALLOW_ALL_USERS is enabled")
    if args.execute and read_allowed_telegram_ids(args.config) != settings.allowed_telegram_ids:
        raise ValueError(
            "runtime whitelist differs from config.yaml; remove the environment override first"
        )

    if not args.execute:
        async with async_session() as session:
            counts = await user_data_counts(session, user_id)
            await session.rollback()
            return {
                "mode": "dry-run",
                "telegram_id": user_id,
                "counts": counts,
                "required_confirmation": expected,
            }

    lease = SingletonLease(engine)
    if not await lease.acquire():
        raise RuntimeError("stop the bot runtime before executing privacy deletion")
    try:
        original_whitelist = read_allowed_telegram_ids(args.config)
        async with async_session() as session:
            whitelist_changed = remove_allowed_telegram_id(args.config, user_id)
            try:
                counts = await delete_user_data(session, user_id)
                await session.commit()
            except Exception as database_error:
                await session.rollback()
                if whitelist_changed:
                    try:
                        write_allowed_telegram_ids(args.config, original_whitelist)
                    except Exception as config_error:
                        raise RuntimeError(
                            "database deletion failed and whitelist rollback failed"
                        ) from config_error
                raise database_error
    finally:
        await lease.release()

    return {
        "mode": "executed",
        "telegram_id": user_id,
        "deleted_counts": counts,
        "verification": "all-user-data-zero",
        "whitelist_changed": whitelist_changed,
    }


async def main() -> None:
    args = parse_args()
    try:
        result = await run(args)
        result["timestamp"] = datetime.now(UTC).isoformat()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
