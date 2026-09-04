#!/usr/bin/env python3
"""Dry-run-first operator workflow for a user's privacy deletion request."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class PreparedDeletion:
    operation_key: str
    operation: dict[str, Any]
    original_whitelist: list[int]

    @property
    def whitelist_changed(self) -> bool:
        return bool(self.operation.get("whitelist_changed"))


def _validate_execution_request(
    args: argparse.Namespace, user_id: int, expected: str
) -> None:
    if not args.execute:
        return
    if args.confirm != expected:
        raise ValueError(f"--execute requires --confirm {expected}")
    if user_id in settings.admin_telegram_ids:
        raise ValueError("refusing to delete an administrator account")
    if settings.allow_all_users:
        raise ValueError("refusing deletion while ALLOW_ALL_USERS is enabled")
    if read_allowed_telegram_ids(args.config) != settings.allowed_telegram_ids:
        raise ValueError(
            "runtime whitelist differs from config.yaml; remove the environment override first"
        )


async def _dry_run(user_id: int, expected: str) -> dict[str, object]:
    async with async_session() as session:
        counts = await user_data_counts(session, user_id)
        await session.rollback()
    return {
        "mode": "dry-run",
        "telegram_id": user_id,
        "counts": counts,
        "required_confirmation": expected,
    }


async def _completed_result(
    user_id: int, config: Path
) -> dict[str, object] | None:
    async with async_session() as session:
        current_counts = await user_data_counts(session, user_id)
        await session.rollback()
    current_whitelist = read_allowed_telegram_ids(config)
    if any(current_counts.values()) or user_id in current_whitelist:
        return None
    return {
        "mode": "already-completed",
        "telegram_id": user_id,
        "deleted_counts": {},
        "verification": "all-user-data-zero",
        "verification_counts": current_counts,
        "whitelist_changed": False,
    }


async def _prepare_deletion(
    user_id: int, config: Path
) -> tuple[PreparedDeletion | None, dict[str, object] | None]:
    operation_key = f"privacy.deletion.{user_id}"
    async with async_session() as session:
        existing = await get_operational_state(session, operation_key)
        operation = dict(existing.value) if existing and existing.value else {}
    if operation.get("phase") == "completed":
        if result := await _completed_result(user_id, config):
            return None, result
        operation = {}
    if not operation or operation.get("phase") == "rolled_back":
        original_whitelist = read_allowed_telegram_ids(config)
        operation = {
            "operation_id": str(uuid.uuid4()),
            "phase": "prepared",
            "telegram_id": user_id,
            "original_whitelist": original_whitelist,
            "whitelist_changed": user_id in original_whitelist,
        }
        async with async_session() as session:
            await set_operational_state(session, operation_key, operation)
    else:
        original_whitelist = list(operation.get("original_whitelist", []))
    return PreparedDeletion(operation_key, operation, original_whitelist), None


async def _revoke_access(prepared: PreparedDeletion, user_id: int, config: Path) -> None:
    current = read_allowed_telegram_ids(config)
    revoked = [value for value in prepared.original_whitelist if value != user_id]
    if current not in (prepared.original_whitelist, revoked):
        raise RuntimeError(
            "whitelist changed after deletion journal was prepared; operator reconciliation required"
        )
    if user_id in current:
        remove_allowed_telegram_id(config, user_id)
    async with async_session() as session:
        await set_operational_state(
            session,
            prepared.operation_key,
            {**prepared.operation, "phase": "access_revoked"},
        )


async def _mark_rollback(prepared: PreparedDeletion, user_id: int) -> None:
    async with async_session() as session:
        await set_operational_state(
            session,
            prepared.operation_key,
            {
                "operation_id": prepared.operation.get("operation_id"),
                "phase": "rolled_back",
                "telegram_id": user_id,
                "whitelist_changed": prepared.whitelist_changed,
            },
        )


async def _delete_data(
    prepared: PreparedDeletion, user_id: int, config: Path
) -> dict[str, int]:
    async with async_session() as session:
        try:
            counts = await delete_user_data(session, user_id)
            await set_operational_state(
                session,
                prepared.operation_key,
                {
                    "operation_id": prepared.operation.get("operation_id"),
                    "phase": "completed",
                    "telegram_id": user_id,
                    "deleted_counts": counts,
                    "whitelist_changed": prepared.whitelist_changed,
                },
                commit=False,
            )
            await session.commit()
            return counts
        except Exception as database_error:
            await session.rollback()
            if prepared.whitelist_changed:
                try:
                    write_allowed_telegram_ids(config, prepared.original_whitelist)
                except Exception as config_error:
                    raise RuntimeError(
                        "database deletion failed and whitelist rollback failed"
                    ) from config_error
            await _mark_rollback(prepared, user_id)
            raise database_error


async def _verification_counts(user_id: int) -> dict[str, int]:
    async with async_session() as session:
        counts = await user_data_counts(session, user_id)
        await session.rollback()
    if remaining := {name: count for name, count in counts.items() if count}:
        raise RuntimeError(f"post-delete verification failed: {remaining}")
    return counts


async def run(args: argparse.Namespace) -> dict[str, object]:
    user_id = args.telegram_id
    expected = confirmation_phrase(user_id)
    _validate_execution_request(args, user_id, expected)
    if not args.execute:
        return await _dry_run(user_id, expected)

    lease = SingletonLease(engine)
    if not await lease.acquire():
        raise RuntimeError("stop the bot runtime before executing privacy deletion")
    try:
        prepared, completed = await _prepare_deletion(user_id, args.config)
        if completed is not None:
            return completed
        if prepared is None:
            raise RuntimeError("deletion journal preparation returned no operation")
        await _revoke_access(prepared, user_id, args.config)
        counts = await _delete_data(prepared, user_id, args.config)
        verification_counts = await _verification_counts(user_id)
    finally:
        await lease.release()

    return {
        "mode": "executed",
        "telegram_id": user_id,
        "deleted_counts": counts,
        "verification": "all-user-data-zero",
        "verification_counts": verification_counts,
        "whitelist_changed": prepared.whitelist_changed,
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
