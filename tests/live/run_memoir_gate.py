#!/usr/bin/env python3
"""Targeted production Telegram gate for pending memoir ownership."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


async def _client(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(args.userbot_dir))
    sys.path.insert(0, str(args.userbot_dir / "tests_dailyplanner"))
    from run_dp_test import BOT_USERNAME  # noqa: PLC0415
    from userbot import userbot_session  # noqa: PLC0415

    async with userbot_session() as app:
        me = await app.get_me()
        if me.id != args.user_id:
            raise RuntimeError(f"E2E session user mismatch: {me.id} != {args.user_id}")
        chat = await app.get_chat(BOT_USERNAME)
        bot_user = await app.get_users(BOT_USERNAME)

        last_sent_details: dict[str, int | None] = {}

        async def send_and_wait(text: str, *, reply_to: int | None = None) -> str:
            sent = await app.send_message(
                chat.id,
                text,
                reply_to_message_id=reply_to,
            )
            last_sent_details.update(
                sent_id=sent.id,
                requested_reply_to=reply_to,
                actual_reply_to=(sent.reply_to_message.id if sent.reply_to_message else None),
            )
            for _ in range(90):
                replies = []
                async for message in app.get_chat_history(chat.id, limit=30):
                    sender = getattr(message, "from_user", None)
                    if message.id > sent.id and sender and sender.id == bot_user.id:
                        replies.append(message)
                if replies:
                    first = min(replies, key=lambda item: item.id)
                    return (first.text or first.caption or "").strip()
                await asyncio.sleep(1)
            raise RuntimeError(f"Bot response timeout for marker {args.run_id}")

        async def find_user_visible_prompt_id() -> int:
            for _ in range(30):
                async for message in app.get_chat_history(chat.id, limit=50):
                    sender = getattr(message, "from_user", None)
                    content = message.text or message.caption or ""
                    if sender and sender.id == bot_user.id and args.run_id in content:
                        return message.id
                await asyncio.sleep(1)
            raise RuntimeError(f"Live memoir prompt not visible for marker {args.run_id}")

        task_response = await send_and_wait(f"Надо купить {args.run_id} молоко")
        if "мемуар" in task_response.casefold() or "задач" not in task_response.casefold():
            raise RuntimeError(f"plain task was misrouted: {task_response!r}")

        done_response = await send_and_wait(f"{args.run_id} молоко — сделал")
        if "мемуар" in done_response.casefold() or not any(
            word in done_response.casefold() for word in ("выполн", "готов", "закры")
        ):
            raise RuntimeError(f"plain completion was misrouted: {done_response!r}")

        reminder_response = await send_and_wait(
            f"Напомни через 15 минут {args.run_id} воду"
        )
        if "мемуар" in reminder_response.casefold() or "напомин" not in reminder_response.casefold():
            raise RuntimeError(f"plain reminder was misrouted: {reminder_response!r}")

        user_visible_prompt_id = await find_user_visible_prompt_id()
        memoir_response = await send_and_wait(
            f"Сегодня самым ярким событием был live gate {args.run_id}",
            reply_to=user_visible_prompt_id,
        )
        if "записано в мемуарник" not in memoir_response.casefold():
            raise RuntimeError(
                "explicit memoir reply was not saved: "
                f"response={memoir_response!r} sent={last_sent_details!r}"
            )

        print(
            json.dumps(
                {
                    "task": task_response,
                    "completion": done_response,
                    "reminder": reminder_response,
                    "memoir": memoir_response,
                },
                ensure_ascii=False,
            )
        )


async def _orchestrate(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT))
    import pendulum  # noqa: PLC0415
    from aiogram import Bot  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    from bot.application.interactions import interaction_service  # noqa: PLC0415
    from bot.config import settings  # noqa: PLC0415
    from bot.db.engine import async_session, engine  # noqa: PLC0415
    from bot.db.models import (  # noqa: PLC0415
        DiaryEntry,
        MemoirEntry,
        Reminder,
        Task,
    )
    from bot.scheduler.memoir import build_memoir_keyboard  # noqa: PLC0415
    tested_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    release_file = Path.home() / "Library/Application Support/notebook-bot/state/current-release"
    deployed_sha = release_file.read_text(encoding="utf-8").splitlines()[0]
    if deployed_sha != tested_sha:
        raise RuntimeError(f"SHA mismatch: checkout={tested_sha} production={deployed_sha}")

    configured_ids = settings.yaml_config.get("testing", {}).get("e2e_user_ids", [])
    user_id = args.user_id or (configured_ids[0] if len(configured_ids) == 1 else None)
    if not user_id or user_id not in configured_ids:
        raise RuntimeError("Select one configured testing.e2e_user_ids account")

    run_id = f"DP-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    token = f"memoir-live-{run_id}"
    today = pendulum.now("Europe/Moscow").date()
    prompt_id: int | None = None
    async with async_session() as session:
        existing = (
            await session.execute(
                select(MemoirEntry).where(
                    MemoirEntry.user_id == user_id,
                    MemoirEntry.event_date == today,
                    MemoirEntry.period_type == "day",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError("Dedicated E2E account already has today's memoir entry")

    claimed = await interaction_service.claim(
        user_id,
        "memoir",
        {"session_token": token, "phase": "reserved", "run_id": run_id},
        60,
    )
    if claimed is None:
        raise RuntimeError("Dedicated E2E account has another active interaction")

    try:
        async with Bot(settings.bot_token) as bot:
            prompt = await bot.send_message(
                user_id,
                "📔 Live memoir gate "
                f"{run_id}. Ответь через Reply после проверочных команд.",
                reply_markup=build_memoir_keyboard(token),
            )
            prompt_id = prompt.message_id
        transitioned = await interaction_service.transition(
            user_id,
            "memoir",
            "memoir",
            {"session_token": token, "message_id": prompt_id, "run_id": run_id},
            60,
            token,
        )
        if transitioned is None:
            raise RuntimeError("Could not attach live prompt to memoir interaction")

        completed = subprocess.run(
            [
                str(args.userbot_dir / ".venv/bin/python"),
                str(Path(__file__).resolve()),
                "--client",
                "--userbot-dir",
                str(args.userbot_dir),
                "--user-id",
                str(user_id),
                "--run-id",
                run_id,
            ],
            text=True,
            capture_output=True,
            timeout=420,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout)
        client_evidence = json.loads(completed.stdout.splitlines()[-1])

        async with async_session() as session:
            tasks = list(
                (
                    await session.execute(
                        select(Task).where(
                            Task.user_id == user_id,
                            Task.title.ilike(f"%{run_id}%"),
                        )
                    )
                ).scalars()
            )
            reminders = list(
                (
                    await session.execute(
                        select(Reminder).where(
                            Reminder.user_id == user_id,
                            Reminder.message.ilike(f"%{run_id}%"),
                        )
                    )
                ).scalars()
            )
            memoirs = list(
                (
                    await session.execute(
                        select(MemoirEntry).where(
                            MemoirEntry.user_id == user_id,
                            MemoirEntry.content.ilike(f"%{run_id}%"),
                        )
                    )
                ).scalars()
            )
            diaries = list(
                (
                    await session.execute(
                        select(DiaryEntry).where(
                            DiaryEntry.user_id == user_id,
                            DiaryEntry.content.ilike(f"%{run_id}%"),
                        )
                    )
                ).scalars()
            )
        oracle = {
            "ok": (
                len(tasks) == 1
                and tasks[0].status == "completed"
                and len(reminders) == 1
                and len(memoirs) == 1
                and len(diaries) == 1
                and await interaction_service.get(user_id, "memoir") is None
            ),
            "task_status": tasks[0].status if len(tasks) == 1 else None,
            "reminders": len(reminders),
            "memoirs": len(memoirs),
            "diaries": len(diaries),
        }
        if not oracle["ok"]:
            raise RuntimeError(f"live memoir DB oracle failed: {oracle!r}")
        print(
            "Live memoir gate:",
            json.dumps(
                {
                    "tested_sha": tested_sha,
                    "run_id": run_id,
                    "responses": client_evidence,
                    "oracle": oracle,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    except Exception:
        diagnostic_state = await interaction_service.get(user_id)
        print(
            "Live memoir diagnostic:",
            json.dumps(
                {
                    "state_type": diagnostic_state.state_type if diagnostic_state else None,
                    "payload": diagnostic_state.payload if diagnostic_state else None,
                    "expected_prompt_id": prompt_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        raise
    finally:
        await interaction_service.clear(user_id, "memoir", token)
        await engine.dispose()
        cleanup_completed = subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/cleanup_e2e_namespace.py"),
                "--user-id",
                str(user_id),
                "--run-id",
                run_id,
                "--execute",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        cleanup_evidence = (
            json.loads(cleanup_completed.stdout)
            if cleanup_completed.returncode == 0
            else {"ok": False, "error": cleanup_completed.stderr}
        )
        cleanup_evidence["ok"] = cleanup_completed.returncode == 0
        print(
            "Live memoir cleanup:",
            json.dumps(cleanup_evidence, sort_keys=True),
        )
        if cleanup_completed.returncode:
            raise RuntimeError("live memoir cleanup failed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--userbot-dir", type=Path, default=Path("/Users/moltbot/Projects/userbot"))
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.client:
        if not args.user_id or not args.run_id:
            raise SystemExit("client mode requires user-id and run-id")
        asyncio.run(_client(args))
    else:
        asyncio.run(_orchestrate(args))


if __name__ == "__main__":
    main()
