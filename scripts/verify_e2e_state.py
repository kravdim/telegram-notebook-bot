#!/usr/bin/env python3
"""Independent PostgreSQL oracle for the credentialed Telegram acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Text, cast, func, or_, select

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import Birthday, DiaryEntry, Note, Reminder, Task
from bot.services.user_deletion import user_data_counts

RUN_ID_RE = re.compile(r"^DP-\d{8}T\d{6}-[a-f0-9]{6}$")


async def _marker_count(session, model, user_id: int, columns, fragment: str) -> int:
    pattern = f"%{fragment}%"
    conditions = [cast(column, Text).ilike(pattern) for column in columns]
    result = await session.execute(
        select(func.count()).select_from(model).where(
            model.user_id == user_id,
            or_(*conditions),
        )
    )
    return result.scalar_one()


async def verify_acceptance(user_id: int, run_id: str) -> dict:
    """Verify critical positive AND-effects and negative non-effects."""
    checks: dict[str, bool] = {}
    async with async_session() as session:
        async def task(fragment: str) -> int:
            return await _marker_count(session, Task, user_id, [Task.title], fragment)

        async def reminder(fragment: str) -> int:
            return await _marker_count(
                session, Reminder, user_id, [Reminder.message], fragment
            )

        async def note(fragment: str) -> int:
            return await _marker_count(
                session, Note, user_id, [Note.title, Note.content], fragment
            )

        # The dedicated account is wiped before every gate. The model may
        # legitimately normalize away the technical run marker, so verify the
        # unique business fragments and exact counts instead of display text.
        checks["multi_intent_task_call"] = await task("пет") == 1
        checks["multi_intent_task_reference"] = await task("справк") == 1
        checks["multi_intent_reminder"] = await reminder("созвон") == 1
        checks["note_and_task_note"] = await note(f"{run_id}-wifi") == 1
        checks["note_and_task_task"] = await task(f"{run_id}-кран") == 1
        checks["live_reminder"] = await reminder(f"{run_id}-чай") >= 1
        checks["explicit_diary"] = (
            await _marker_count(
                session, DiaryEntry, user_id, [DiaryEntry.content], f"{run_id}-дневник"
            )
            == 1
        )
        checks["explicit_note"] = await note(f"{run_id}-заметка") == 1
        checks["birthday"] = (
            await _marker_count(
                session, Birthday, user_id, [Birthday.name, Birthday.note], "пап"
            )
            == 1
        )
        checks["past_task_absent"] = await task(f"{run_id}-вчерашний") == 0
        checks["zero_reminder_absent"] = await reminder(f"{run_id}-ноль") == 0
        checks["invalid_birthday_absent"] = (
            await _marker_count(
                session, Birthday, user_id, [Birthday.name, Birthday.note], "барсик"
            )
            == 0
        )
    return {"phase": "acceptance", "ok": all(checks.values()), "checks": checks}


async def verify_cleanup(user_id: int) -> dict:
    """Prove that only the preserved registration/settings row remains."""
    async with async_session() as session:
        counts = await user_data_counts(session, user_id)
    residual = {
        name: count
        for name, count in counts.items()
        if name != "users" and count != 0
    }
    return {
        "phase": "cleanup",
        "ok": counts.get("users") == 1 and not residual,
        "residual": residual,
        "registration_rows": counts.get("users", 0),
    }


async def run(user_id: int, run_id: str, phase: str) -> dict:
    if user_id <= 0 or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid E2E identity")
    allowed = settings.yaml_config.get("testing", {}).get("e2e_user_ids", [])
    if user_id not in allowed:
        raise ValueError("user is not configured as a dedicated E2E account")
    if phase == "acceptance":
        return await verify_acceptance(user_id, run_id)
    return await verify_cleanup(user_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", choices=("acceptance", "cleanup"), required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.user_id, args.run_id, args.phase))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
