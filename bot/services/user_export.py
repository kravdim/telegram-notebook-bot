"""Versioned export of the same user-owned inventory used by deletion."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID

import pendulum
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Base, DeliveryBatch, DeliveryPart, FsmState, User
from bot.services.export import ExportTooLargeError
from bot.services.user_deletion import user_data_counts

EXPORT_SCHEMA_VERSION = 1


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    return str(value)


def _export_row(dataset: str, row: dict[str, Any]) -> dict[str, Any]:
    exported = {key: _json_value(value) for key, value in row.items()}
    if dataset == "birthdays" and not exported.get("year_known"):
        raw_date = row.get("birth_date")
        if isinstance(raw_date, date):
            exported["birth_date"] = f"--{raw_date.month:02d}-{raw_date.day:02d}"
    return exported


def _inventory_statements(user_id: int) -> list[tuple[str, Any]]:
    statements: list[tuple[str, Any]] = []
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        if "user_id" not in table.c:
            continue
        statements.append(
            (table.name, select(table).where(table.c.user_id == user_id))
        )
    statements.append(
        (
            User.__tablename__,
            select(User.__table__).where(User.telegram_id == user_id),
        )
    )
    statements.append(
        (
            FsmState.__tablename__,
            select(FsmState.__table__).where(
                func.split_part(FsmState.storage_key, ":", 3) == str(user_id)
            ),
        )
    )
    statements.append(
        (
            DeliveryPart.__tablename__,
            select(DeliveryPart.__table__)
            .join(DeliveryBatch, DeliveryPart.batch_id == DeliveryBatch.id)
            .where(DeliveryBatch.user_id == user_id),
        )
    )
    return sorted(statements, key=lambda item: item[0])


def _file_lines(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8") as source:
        yield from source


async def build_user_export_sections(
    session: AsyncSession,
    user_id: int,
    staging_dir: Path,
    *,
    max_bytes: int,
) -> list[tuple[str, Iterable[str]]]:
    """Stage a bounded, count-verified export without retaining rows in memory."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    staging_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    expected_counts = await user_data_counts(session, user_id)
    actual_counts: dict[str, int] = {}
    raw_bytes = 0
    data_sections: list[tuple[str, Iterable[str]]] = []
    for dataset, statement in _inventory_statements(user_id):
        path = staging_dir / f"{dataset}.jsonl"
        count = 0
        result = await session.stream(statement)
        with path.open("x", encoding="utf-8") as output:
            async for row in result.mappings():
                exported = _export_row(dataset, dict(row))
                line = json.dumps(exported, ensure_ascii=False, sort_keys=True) + "\n"
                raw_bytes += len(line.encode("utf-8"))
                if raw_bytes > max_bytes:
                    raise ExportTooLargeError(
                        f"export content exceeds {max_bytes} bytes"
                    )
                output.write(line)
                count += 1
        path.chmod(0o600)
        actual_counts[dataset] = count
        data_sections.append((f"data/{dataset}.jsonl", _file_lines(path)))

    if actual_counts != expected_counts:
        raise RuntimeError(
            "export inventory verification failed: dataset counts do not match"
        )

    manifest = {
        "schema": "dailyplanner-user-export",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": pendulum.now("UTC").to_iso8601_string(),
        "telegram_user_id": user_id,
        "datasets": expected_counts,
    }
    manifest_text = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    if raw_bytes + len(manifest_text.encode("utf-8")) > max_bytes:
        raise ExportTooLargeError(f"export content exceeds {max_bytes} bytes")
    sections: list[tuple[str, Iterable[str]]] = [
        ("manifest.json", [manifest_text])
    ]
    sections.extend(data_sections)
    return sections
