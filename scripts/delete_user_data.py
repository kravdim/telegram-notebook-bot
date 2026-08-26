#!/usr/bin/env python3
"""Dry-run-first operator workflow for a user's privacy deletion request."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.config import BASE_DIR, settings
from bot.db.crud.operational import get_operational_state, set_operational_state
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
        operation_key = f"privacy.deletion.{user_id}"
        async with async_session() as session:
            existing = await get_operational_state(session, operation_key)
            operation = existing.value if existing else None
            if operation and operation.get("phase") == "completed":
                return {
                    "mode": "already-completed",
                    "telegram_id": user_id,
                    "deleted_counts": operation.get("deleted_counts", {}),
                    "verification": "all-user-data-zero",
                    "whitelist_changed": operation.get("whitelist_changed", False),
                }
            if not operation or operation.get("phase") == "rolled_back":
                original_whitelist = read_allowed_telegram_ids(args.config)
                operation = {
                    "phase": "prepared",
                    "telegram_id": user_id,
                    "original_whitelist": original_whitelist,
                    "whitelist_changed": user_id in original_whitelist,
                }
                await set_operational_state(session, operation_key, operation)
            else:
                original_whitelist = list(operation.get("original_whitelist", []))

        current_whitelist = read_allowed_telegram_ids(args.config)
        revoked_whitelist = [value for value in original_whitelist if value != user_id]
        if current_whitelist not in (original_whitelist, revoked_whitelist):
            raise RuntimeError(
                "whitelist changed after deletion journal was prepared; operator reconciliation required"
            )
        operation_whitelist_changed = bool(operation.get("whitelist_changed"))
        if user_id in current_whitelist:
            remove_allowed_telegram_id(args.config, user_id)

        async with async_session() as session:
            await set_operational_state(
                session,
                operation_key,
                {
                    **operation,
                    "phase": "access_revoked",
                    "whitelist_changed": bool(operation.get("whitelist_changed")),
                },
            )

        async with async_session() as session:
            try:
                counts = await delete_user_data(session, user_id)
                await set_operational_state(
                    session,
                    operation_key,
                    {
                        "phase": "completed",
                        "telegram_id": user_id,
                        "deleted_counts": counts,
                        "whitelist_changed": bool(
                            operation.get("whitelist_changed")
                        ),
                    },
                    commit=False,
                )
                await session.commit()
            except Exception as database_error:
                await session.rollback()
                if operation_whitelist_changed:
                    try:
                        write_allowed_telegram_ids(args.config, original_whitelist)
                    except Exception as config_error:
                        raise RuntimeError(
                            "database deletion failed and whitelist rollback failed"
                        ) from config_error
                async with async_session() as journal_session:
                    await set_operational_state(
                        journal_session,
                        operation_key,
                        {
                            "phase": "rolled_back",
                            "telegram_id": user_id,
                            "whitelist_changed": operation_whitelist_changed,
                        },
                    )
                raise database_error
    finally:
        await lease.release()

    return {
        "mode": "executed",
        "telegram_id": user_id,
        "deleted_counts": counts,
        "verification": "all-user-data-zero",
        "whitelist_changed": operation_whitelist_changed,
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
