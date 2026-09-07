"""Durable, resumable delivery of logical multipart Telegram messages."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Sequence

import pendulum
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from bot.db.engine import async_session
from bot.db.models import DeliveryBatch, DeliveryPart
from bot.logging_safety import error_type

logger = logging.getLogger(__name__)


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
    terminal: bool = False


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
    expires_at=None,
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
                expires_at=expires_at or pendulum.now("UTC").add(hours=24),
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
    expires_at=None,
) -> DeliveryResult:
    """Send pending parts and persist progress after every Telegram response.

    The boundary remains at-least-once: a process death after Telegram accepts a
    message but before the database commit can repeat that one part.
    """
    batch_id = await _ensure_batch(delivery_key, user_id, kind, parts, expires_at)
    return await _deliver_existing_batch(bot, batch_id, lease_seconds)


async def _deliver_existing_batch(bot, batch_id: uuid.UUID, lease_seconds: int = 300) -> DeliveryResult:
    """Продолжить сохранённую доставку без повторного построения payload."""
    now = pendulum.now("UTC")
    lease_token = uuid.uuid4()

    async with async_session() as session:
        await session.execute(update(DeliveryBatch).where(
            DeliveryBatch.id == batch_id,
            DeliveryBatch.status.in_(("pending", "delivering")),
            DeliveryBatch.expires_at <= now,
            or_(DeliveryBatch.lease_token.is_(None), DeliveryBatch.lease_expires_at < now),
        ).values(status="expired", lease_token=None, lease_expires_at=None))
        claimed = await session.execute(
            update(DeliveryBatch)
            .where(
                DeliveryBatch.id == batch_id,
                DeliveryBatch.status.in_(("pending", "delivering")),
                or_(DeliveryBatch.next_attempt_at.is_(None), DeliveryBatch.next_attempt_at <= now),
                or_(DeliveryBatch.expires_at.is_(None), DeliveryBatch.expires_at > now),
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
            terminal=status in ("failed", "expired"),
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
            attempts = await session.scalar(select(DeliveryBatch.attempts).where(
                DeliveryBatch.id == batch_id, DeliveryBatch.lease_token == lease_token
            ))
            terminal = isinstance(exc, (TelegramBadRequest, TelegramForbiddenError)) or (attempts or 0) >= 8
            await session.execute(
                update(DeliveryPart)
                .where(
                    DeliveryPart.id == part.id,
                    DeliveryPart.status == "pending",
                    DeliveryPart.batch_id.in_(
                        select(DeliveryBatch.id).where(
                            DeliveryBatch.id == batch_id,
                            DeliveryBatch.lease_token == lease_token,
                        )
                    ),
                )
                .values(
                    attempts=DeliveryPart.attempts + 1,
                    last_error=error_type(exc),
                )
            )
            await session.execute(
                update(DeliveryBatch)
                .where(
                    DeliveryBatch.id == batch_id,
                    DeliveryBatch.lease_token == lease_token,
                )
                .values(
                    status="failed" if terminal else "pending",
                    next_attempt_at=failed_at.add(seconds=min(3600, 30 * 2 ** min(attempts or 1, 7))),
                    lease_token=None,
                    lease_expires_at=None,
                    last_error=error_type(exc),
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


async def resume_pending_deliveries(bot) -> None:
    """Восстановить outbox после рестарта независимо от окна исходного scheduler."""
    now = pendulum.now("UTC")
    async with async_session() as session:
        batch_ids = list((await session.scalars(select(DeliveryBatch.id).where(
            DeliveryBatch.status.in_(("pending", "delivering")),
            or_(DeliveryBatch.next_attempt_at.is_(None), DeliveryBatch.next_attempt_at <= now),
            or_(DeliveryBatch.lease_token.is_(None), DeliveryBatch.lease_expires_at < now),
        ).order_by(DeliveryBatch.created_at).limit(50))).all())
    for batch_id in batch_ids:
        try:
            await _deliver_existing_batch(bot, batch_id)
        except Exception as exc:
            logger.warning("Outbox retry failed: error_type=%s", error_type(exc))
