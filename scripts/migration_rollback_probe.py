#!/usr/bin/env python3
"""Synthetic domain checks imported from an explicitly selected release tree."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

OWNER = 8_290_000_001
TASK_ID = uuid.UUID("dc5d6fc0-4fbb-40c3-b69e-38d25fe1cdd1")


async def probe(mode: str, project: Path) -> dict:
    import pendulum

    from bot.db import models
    from bot.db.crud.reminders import get_pending_reminders
    from bot.db.engine import async_session, engine
    from bot.db.models import Note, Reminder, Task, User

    if Path(models.__file__).resolve() != project / "bot/db/models.py":
        raise RuntimeError("Probe imported the wrong release tree")
    try:
        async with async_session() as session:
            if mode == "seed":
                session.add(User(telegram_id=OWNER, username="rollback-synthetic",
                                 privacy_notice_version=1, cloud_processing_enabled=True))
                await session.flush()
                session.add(Task(id=TASK_ID, user_id=OWNER, title="baseline task"))
                session.add(Note(user_id=OWNER, content="baseline note"))
                await session.commit()
            elif mode == "candidate":
                from bot.db.models import ProcessedRequest

                due = pendulum.now("UTC").subtract(minutes=1)
                for status in ("failed", "pending"):
                    session.add(Reminder(
                        user_id=OWNER, message=f"synthetic {status}",
                        remind_at=due, occurrence_at=due, status=status,
                        is_sent=False, lease_token=uuid.uuid4() if status == "pending" else None,
                        lease_expires_at=pendulum.now("UTC").add(minutes=5) if status == "pending" else None,
                    ))
                session.add(ProcessedRequest(request_key="rollback-synthetic-request", user_id=OWNER,
                                             status="failed", action_plan=[{"name": "create_note"}],
                                             action_results={}))
                session.add(Note(user_id=OWNER, content="candidate quarantine canary"))
                await session.commit()
            elif mode == "legacy-hazard":
                pending = await get_pending_reminders(session, pendulum.now("UTC"))
                statuses = sorted(row.status for row in pending if row.user_id == OWNER)
                if statuses != ["failed", "pending"]:
                    raise RuntimeError("Expected old sender to pick failed and leased occurrences")
                return {"legacy_sender_unsafe": True, "selected_statuses": statuses}
            elif mode == "restored":
                from bot.services.tasks import complete_task_workflow

                notes = list(await session.scalars(select(Note.content).where(Note.user_id == OWNER)))
                task = await session.get(Task, TASK_ID)
                if notes != ["baseline note"] or task is None or task.title != "baseline task":
                    raise RuntimeError("Restored domain data differs from pre-migration snapshot")
                result = await complete_task_workflow(session, TASK_ID, OWNER)
                if not result.completed or result.task is None or result.task.status != "done":
                    raise RuntimeError("Previous release cannot complete restored task")
                session.add(Note(user_id=OWNER, content="old release write after restore"))
                await session.commit()
            elif mode == "quarantine":
                notes = list(await session.scalars(select(Note.content).where(Note.user_id == OWNER)))
                if sorted(notes) != ["baseline note", "candidate quarantine canary"]:
                    raise RuntimeError("Failed candidate state was lost during restore")
                owner = await session.get(User, OWNER)
                if owner is None or not owner.cloud_processing_enabled or owner.privacy_notice_version != 1:
                    raise RuntimeError("Rejected downgrade partially modified consent")
            head = await session.scalar(text("SELECT version_num FROM alembic_version"))
            return {"mode": mode, "schema_head": head, "ok": True}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--mode", choices=["seed", "candidate", "legacy-hazard", "restored", "quarantine"], required=True)
    args = parser.parse_args()
    url = make_url(os.environ.get("DATABASE_URL", "sqlite://"))
    if (os.environ.get("DAILYPLANNER_MIGRATION_DRILL") != "1" or url.host != "127.0.0.1"
            or url.database not in {"migration_drill", "migration_restored"} or url.username != "drill"):
        raise SystemExit("Only the disposable migration drill databases are allowed")
    project = args.project.resolve()
    sys.path.insert(0, str(project))
    print(json.dumps(asyncio.run(probe(args.mode, project)), sort_keys=True))


if __name__ == "__main__":
    main()
