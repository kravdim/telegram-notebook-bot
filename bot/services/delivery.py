"""Durable, resumable delivery of logical multipart Telegram messages."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Sequence

import pendulum
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from bot.db.engine import async_session
from bot.db.models import DeliveryBatch, DeliveryPart


@dataclass(frozen=True)
class DeliveryPartSpec:
    chat_id: int
    text: str
    parse_mode: str | None = None
    reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None


@dataclass(frozen=True)
class DeliveryResult:
    completed: bool
    busy: bool = False
    already_completed: bool = False
    message_ids: tuple[int | None, ...] = ()


class _DeliveryLeaseLost(RuntimeError):
    """The current worker no longer owns the durable delivery batch."""


def _serialize_markup(markup) -> dict[str, Any] | None:
    if markup is None or isinstance(markup, dict):
        return markup
    return markup.model_dump(mode="json", exclude_none=True)


async def _ensure_batch(
    delivery_key: str,
    user_id: int,
    kind: str,
    parts: Sequence[DeliveryPartSpec],
) -> uuid.UUID:
    """Create an immutable batch once; retries use its persisted payload."""
    if not parts:
        raise ValueError("delivery batch must contain at least one part")

    batch_id = uuid.uuid4()
    async with async_session() as session:
        created = await session.execute(
            insert(DeliveryBatch)
            .values(
                id=batch_id,
                delivery_key=delivery_key,
                user_id=user_id,
                kind=kind,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=[DeliveryBatch.delivery_key])
            .returning(DeliveryBatch.id)
        )
        actual_id = created.scalar_one_or_none()
        if actual_id:
            session.add_all(
                DeliveryPart(
                    batch_id=actual_id,
                    position=position,
                    chat_id=part.chat_id,
                    text=part.text,
                    parse_mode=part.parse_mode,
                    reply_markup=_serialize_markup(part.reply_markup),
                )
                for position, part in enumerate(parts)
            )
            await session.commit()
            return actual_id

        existing = await session.execute(
            select(DeliveryBatch.id).where(
                DeliveryBatch.delivery_key == delivery_key,
                DeliveryBatch.user_id == user_id,
                DeliveryBatch.kind == kind,
            )
        )
        existing_id = existing.scalar_one_or_none()
        if existing_id is None:
            raise ValueError("delivery key already belongs to another user or kind")
        return existing_id


async def deliver_batch(
    bot,
    *,
    delivery_key: str,
    user_id: int,
    kind: str,
    parts: Sequence[DeliveryPartSpec],
    lease_seconds: int = 300,
) -> DeliveryResult:
    """Send pending parts and persist progress after every Telegram response.

    The boundary remains at-least-once: a process death after Telegram accepts a
    message but before the database commit can repeat that one part.
    """
    batch_id = await _ensure_batch(delivery_key, user_id, kind, parts)
    now = pendulum.now("UTC")
    lease_token = uuid.uuid4()

    async with async_session() as session:
        claimed = await session.execute(
            update(DeliveryBatch)
            .where(
                DeliveryBatch.id == batch_id,
                DeliveryBatch.status != "delivered",
                or_(
                    DeliveryBatch.lease_token.is_(None),
                    DeliveryBatch.lease_expires_at.is_(None),
                    DeliveryBatch.lease_expires_at < now,
                ),
            )
            .values(
                status="delivering",
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempts=DeliveryBatch.attempts + 1,
                last_error=None,
                updated_at=now,
            )
            .returning(DeliveryBatch.id)
        )
        owns_lease = claimed.scalar_one_or_none() is not None
        await session.commit()

    if not owns_lease:
        async with async_session() as session:
            status = await session.scalar(
                select(DeliveryBatch.status).where(DeliveryBatch.id == batch_id)
            )
            ids = tuple(
                (await session.execute(
                    select(DeliveryPart.telegram_message_id)
                    .where(DeliveryPart.batch_id == batch_id)
                    .order_by(DeliveryPart.position)
                )).scalars().all()
            )
        return DeliveryResult(
            completed=status == "delivered",
            busy=status == "delivering",
            already_completed=status == "delivered",
            message_ids=ids,
        )

    async with async_session() as session:
        pending = list(
            (await session.execute(
                select(DeliveryPart)
                .where(
                    DeliveryPart.batch_id == batch_id,
                    DeliveryPart.status == "pending",
                )
                .order_by(DeliveryPart.position)
            )).scalars().all()
        )

    try:
        for part in pending:
            markup = (
                InlineKeyboardMarkup.model_validate(part.reply_markup)
                if part.reply_markup
                else None
            )
            sent = await bot.send_message(
                chat_id=part.chat_id,
                text=part.text,
                parse_mode=part.parse_mode,
                reply_markup=markup,
            )
            delivered_at = pendulum.now("UTC")
            async with async_session() as session:
                renewed = await session.execute(
                    update(DeliveryBatch)
                    .where(
                        DeliveryBatch.id == batch_id,
                        DeliveryBatch.lease_token == lease_token,
                        DeliveryBatch.lease_expires_at >= delivered_at,
                    )
                    .values(
                        lease_expires_at=delivered_at
                        + timedelta(seconds=lease_seconds),
                        updated_at=delivered_at,
                    )
                    .returning(DeliveryBatch.id)
                )
                if renewed.scalar_one_or_none() is None:
                    await session.rollback()
                    raise _DeliveryLeaseLost
                recorded = await session.execute(
                    update(DeliveryPart)
                    .where(
                        DeliveryPart.id == part.id,
                        DeliveryPart.status == "pending",
                    )
                    .values(
                        status="delivered",
                        telegram_message_id=sent.message_id,
                        attempts=DeliveryPart.attempts + 1,
                        last_error=None,
                        delivered_at=delivered_at,
                    )
                    .returning(DeliveryPart.id)
                )
                if recorded.scalar_one_or_none() is None:
                    await session.rollback()
                    raise _DeliveryLeaseLost
                await session.commit()
    except _DeliveryLeaseLost:
        async with async_session() as session:
            status = await session.scalar(
                select(DeliveryBatch.status).where(DeliveryBatch.id == batch_id)
            )
            ids = tuple(
                (
                    await session.execute(
                        select(DeliveryPart.telegram_message_id)
                        .where(DeliveryPart.batch_id == batch_id)
                        .order_by(DeliveryPart.position)
                    )
                )
                .scalars()
                .all()
            )
        return DeliveryResult(
            completed=status == "delivered",
            busy=status == "delivering",
            already_completed=status == "delivered",
            message_ids=ids,
        )
    except Exception as exc:
        failed_at = pendulum.now("UTC")
        async with async_session() as session:
            await session.execute(
                update(DeliveryPart)
                .where(DeliveryPart.id == part.id)
                .values(
                    attempts=DeliveryPart.attempts + 1,
                    last_error=str(exc)[:1000],
                )
            )
            await session.execute(
                update(DeliveryBatch)
                .where(
                    DeliveryBatch.id == batch_id,
                    DeliveryBatch.lease_token == lease_token,
                )
                .values(
                    status="pending",
                    lease_token=None,
                    lease_expires_at=None,
                    last_error=str(exc)[:1000],
                    updated_at=failed_at,
                )
            )
            await session.commit()
        raise

    completed_at = pendulum.now("UTC")
    async with async_session() as session:
        completed = await session.execute(
            update(DeliveryBatch)
            .where(
                DeliveryBatch.id == batch_id,
                DeliveryBatch.lease_token == lease_token,
            )
            .values(
                status="delivered",
                lease_token=None,
                lease_expires_at=None,
                completed_at=completed_at,
                updated_at=completed_at,
            )
            .returning(DeliveryBatch.id)
        )
        owns_completion = completed.scalar_one_or_none() is not None
        ids = tuple(
            (await session.execute(
                select(DeliveryPart.telegram_message_id)
                .where(DeliveryPart.batch_id == batch_id)
                .order_by(DeliveryPart.position)
            )).scalars().all()
        )
        await session.commit()
    return DeliveryResult(
        completed=owns_completion,
        busy=not owns_completion,
        message_ids=ids,
    )
