#!/usr/bin/env python3
"""Delete artifacts belonging to one explicit DailyPlanner E2E run marker."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Text, cast, delete, or_, select, true, update

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import (
    Birthday,
    DeliveryBatch,
    DeliveryPart,
    DiaryEntry,
    FsmState,
    InteractionState,
    LlmLog,
    MemoirEntry,
    Note,
    ProcessedRequest,
    Project,
    Reminder,
    Task,
    TimeTrackingEntry,
    Trip,
    User,
)
from bot.privacy import PRIVACY_NOTICE_VERSION

RUN_ID_RE = re.compile(r"^DP-\d{8}T\d{6}-[a-f0-9]{6}$")


async def cleanup(
    user_id: int,
    run_id: str,
    *,
    execute: bool,
    all_user_data: bool = False,
) -> dict[str, int]:
    """Delete only rows scoped by both user and exact run marker."""
    if user_id <= 0:
        raise ValueError("user ID must be positive")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid E2E run ID")
    if all_user_data:
        allowed = settings.yaml_config.get("testing", {}).get("e2e_user_ids", [])
        if user_id not in allowed:
            raise ValueError("user is not configured as a dedicated E2E account")

    pattern = f"%{run_id}%"
    counts: dict[str, int] = {}

    async with async_session() as session:
        project_ids = list(
            (await session.execute(
                select(Project.id).where(
                    Project.user_id == user_id,
                    true() if all_user_data else or_(
                        Project.title.ilike(pattern),
                        Project.description.ilike(pattern),
                    ),
                )
            )).scalars().all()
        )
        trip_ids = list(
            (await session.execute(
                select(Trip.id).where(
                    Trip.user_id == user_id,
                    true() if all_user_data else or_(
                        Trip.title.ilike(pattern), Trip.destination.ilike(pattern)
                    ),
                )
            )).scalars().all()
        )
        task_ids = list(
            (await session.execute(
                select(Task.id).where(
                    Task.user_id == user_id,
                    true() if all_user_data else or_(
                        Task.title.ilike(pattern),
                        Task.project_id.in_(project_ids),
                        Task.trip_id.in_(trip_ids),
                    ),
                )
            )).scalars().all()
        )
        delivery_batch_ids = list(
            (await session.execute(
                select(DeliveryBatch.id)
                .join(DeliveryPart, DeliveryPart.batch_id == DeliveryBatch.id)
                .where(
                    DeliveryBatch.user_id == user_id,
                    true() if all_user_data else DeliveryPart.text.ilike(pattern),
                )
                .distinct()
            )).scalars().all()
        )

        statements = [
            (
                "delivery_batches",
                delete(DeliveryBatch).where(DeliveryBatch.id.in_(delivery_batch_ids)),
            ),
            (
                "reminders",
                delete(Reminder).where(
                    Reminder.user_id == user_id,
                    true() if all_user_data else or_(
                        Reminder.message.ilike(pattern), Reminder.task_id.in_(task_ids)
                    ),
                ),
            ),
            (
                "time_tracking_entries",
                delete(TimeTrackingEntry).where(
                    TimeTrackingEntry.user_id == user_id,
                    true() if all_user_data else TimeTrackingEntry.activity_text.ilike(pattern),
                ),
            ),
            (
                "tasks",
                delete(Task).where(Task.id.in_(task_ids), Task.user_id == user_id),
            ),
            (
                "notes",
                delete(Note).where(
                    Note.user_id == user_id,
                    true() if all_user_data else or_(
                        Note.title.ilike(pattern), Note.content.ilike(pattern)
                    ),
                ),
            ),
            (
                "diary_entries",
                delete(DiaryEntry).where(
                    DiaryEntry.user_id == user_id,
                    true() if all_user_data else DiaryEntry.content.ilike(pattern),
                ),
            ),
            (
                "memoir_entries",
                delete(MemoirEntry).where(
                    MemoirEntry.user_id == user_id,
                    true() if all_user_data else MemoirEntry.content.ilike(pattern),
                ),
            ),
            (
                "birthdays",
                delete(Birthday).where(
                    Birthday.user_id == user_id,
                    true() if all_user_data else or_(
                        Birthday.name.ilike(pattern), Birthday.note.ilike(pattern)
                    ),
                ),
            ),
            (
                "projects",
                delete(Project).where(
                    Project.id.in_(project_ids), Project.user_id == user_id
                ),
            ),
            (
                "trips",
                delete(Trip).where(Trip.id.in_(trip_ids), Trip.user_id == user_id),
            ),
            (
                "interaction_states",
                delete(InteractionState).where(
                    InteractionState.user_id == user_id,
                    true() if all_user_data else cast(InteractionState.payload, Text).ilike(pattern),
                ),
            ),
            (
                "fsm_states",
                delete(FsmState).where(
                    FsmState.storage_key.like(f"%:{user_id}:{user_id}:%")
                    if all_user_data
                    else cast(FsmState.data, Text).ilike(pattern)
                ),
            ),
            (
                "llm_logs",
                delete(LlmLog).where(
                    LlmLog.user_id == user_id,
                    true() if all_user_data else or_(
                        cast(LlmLog.input_messages, Text).ilike(pattern),
                        LlmLog.output_content.ilike(pattern),
                        cast(LlmLog.function_call, Text).ilike(pattern),
                    ),
                ),
            ),
        ]
        if all_user_data:
            statements.append(
                (
                    "processed_requests",
                    delete(ProcessedRequest).where(ProcessedRequest.user_id == user_id),
                )
            )
        for name, statement in statements:
            result = await session.execute(statement)
            counts[name] = int(getattr(result, "rowcount", 0) or 0)

        if all_user_data:
            reset = await session.execute(
                update(User)
                .where(User.telegram_id == user_id)
                .values(
                    focus_until=None,
                    privacy_notice_version=PRIVACY_NOTICE_VERSION,
                    cloud_processing_enabled=True,
                )
            )
            counts["users_reset"] = int(getattr(reset, "rowcount", 0) or 0)

        if execute:
            await session.commit()
        else:
            await session.rollback()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="commit deletion; without this flag the transaction is rolled back",
    )
    parser.add_argument(
        "--all-user-data",
        action="store_true",
        help="wipe the configured dedicated E2E account namespace",
    )
    args = parser.parse_args()
    counts = asyncio.run(
        cleanup(
            args.user_id,
            args.run_id,
            execute=args.execute,
            all_user_data=args.all_user_data,
        )
    )
    print(json.dumps({"executed": args.execute, "deleted": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
