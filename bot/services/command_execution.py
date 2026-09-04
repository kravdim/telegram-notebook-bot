"""Persisted plans and atomic effect/result records for retryable requests."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict
from typing import Literal

from sqlalchemy import select

from bot.application.command_bus import CommandResult
from bot.db.engine import async_session, command_transaction
from bot.db.models import ProcessedRequest

active_request: ContextVar[str | None] = ContextVar("active_request", default=None)


async def saved_plan(user_id: int) -> list[dict] | None:
    key = active_request.get()
    if key is None:
        return None
    async with async_session() as session:
        return await session.scalar(select(ProcessedRequest.action_plan).where(
            ProcessedRequest.request_key == key, ProcessedRequest.user_id == user_id,
        ))


async def persist_plan(user_id: int, proposed: list[dict]) -> list[dict]:
    key = active_request.get()
    if key is None:
        return proposed
    async with async_session() as session:
        row = await session.scalar(select(ProcessedRequest).where(
            ProcessedRequest.request_key == key, ProcessedRequest.user_id == user_id,
        ).with_for_update())
        if row is None:
            raise RuntimeError("Cannot mutate without a durable request reservation")
        if row.action_plan is None:
            row.action_plan = proposed
        await session.commit()
        return row.action_plan


async def execute_action(
    user_id: int, position: int, execute: Callable[[], Awaitable[CommandResult]],
    *, phase: Literal["effect", "project_tasks"] = "effect",
) -> CommandResult:
    key = active_request.get()
    if key is None:
        return await execute()
    async with async_session() as session:
        row = await session.scalar(select(ProcessedRequest).where(
            ProcessedRequest.request_key == key, ProcessedRequest.user_id == user_id,
        ).with_for_update())
        if row is None or row.action_plan is None:
            raise RuntimeError("Missing durable action plan")
        if not 0 <= position < len(row.action_plan):
            raise ValueError("Action position outside durable plan")
        action_id = str(position) if phase == "effect" else f"{position}:{phase}"
        if phase == "project_tasks" and row.action_results.get(str(position), {}).get("kind") != "project_created":
            raise ValueError("Project decomposition requires a completed project creation")
        if action_id in row.action_results:
            return CommandResult(**row.action_results[action_id])
        async with command_transaction(session):
            result = await execute()
        if result.kind == "error":
            await session.rollback()
            return result
        row.action_results = {**row.action_results, action_id: asdict(result)}
        await session.commit()
        return result
