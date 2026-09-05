"""Logical data guard for the reviewed additive maintenance migration chain."""

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# These are reviewed migration effects, not configurable SQL from a journal.
# Unknown additions fail closed; new recovery-bearing fields must remain empty.
ADDITIONS = {
    ("reminders", "series_timezone"): (
        "(SELECT u.timezone FROM public.users u WHERE u.telegram_id = t.user_id)"
    ),
    ("reminders", "lease_token"): "NULL",
    ("reminders", "lease_expires_at"): "NULL",
    ("reminders", "next_attempt_at"): "NULL",
    ("processed_requests", "action_plan"): "NULL",
    ("processed_requests", "action_results"): "'{}'::jsonb",
    ("users", "privacy_provider_fingerprint"): "NULL",
}


async def columns(connection: AsyncConnection) -> dict[str, list[str]]:
    rows = await connection.execute(text(
        "SELECT c.table_name, c.column_name FROM information_schema.columns c "
        "JOIN information_schema.tables t USING (table_catalog, table_schema, table_name) "
        "WHERE c.table_schema='public' AND t.table_type='BASE TABLE' "
        "AND c.table_name != 'alembic_version' ORDER BY c.table_name, c.ordinal_position"
    ))
    result: dict[str, list[str]] = {}
    for table, column in rows:
        result.setdefault(table, []).append(column)
    if not result:
        raise RuntimeError("Maintenance guard requires visible application tables")
    return result


async def _check_additions(connection: AsyncConnection, baseline: dict, current: dict) -> None:
    if set(baseline) != set(current):
        raise RuntimeError("Maintenance guard: table set changed")
    quote = connection.dialect.identifier_preparer.quote_identifier
    for table, old_columns in baseline.items():
        if not set(old_columns).issubset(current[table]):
            raise RuntimeError("Maintenance guard: baseline column missing")
        for column in set(current[table]) - set(old_columns):
            expected = ADDITIONS.get((table, column))
            if expected is None:
                raise RuntimeError("Maintenance guard: unreviewed column added")
            # Identifiers are dialect-quoted; expected comes only from ADDITIONS.
            changed = await connection.scalar(text(
                f"SELECT EXISTS (SELECT 1 FROM public.{quote(table)} t "  # nosec B608
                f"WHERE t.{quote(column)} IS DISTINCT FROM {expected})"
            ))
            if changed:
                raise RuntimeError("Maintenance guard: new column contains post-snapshot data")


async def capture(connection: AsyncConnection, baseline: dict | None = None) -> dict:
    """Caller owns a REPEATABLE READ transaction; journal contains only hashes.

    Compare old columns plus explicitly reviewed backfills, never blindly ignore
    newly added fields. Sequence gaps are not treated as user data changes.
    """
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await connection.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
    current = await columns(connection)
    selected = current if baseline is None else baseline["columns"]
    if baseline is not None:
        await _check_additions(connection, selected, current)
    digest = hashlib.sha256()
    quote = connection.dialect.identifier_preparer.quote_identifier
    for table, names in sorted(selected.items()):
        digest.update(json.dumps([table, names], separators=(",", ":")).encode())
        projection = ", ".join(quote(name) for name in names)
        # Hash each row in PostgreSQL and stream sorted hashes: bounded Python
        # memory and no user content in logs, manifest or process arguments.
        # Every interpolated table/column identifier is dialect-quoted above.
        rows = await connection.stream(text(
            "SELECT encode(sha256(convert_to(row_to_json(r)::text, 'UTF8')), 'hex') "  # nosec B608
            f"FROM (SELECT {projection} FROM public.{quote(table)}) r ORDER BY 1"
        ))
        async for row in rows:
            digest.update(row[0].encode("ascii"))
    result = {"columns": selected, "sha256": digest.hexdigest()}
    if baseline is not None and result != baseline:
        raise RuntimeError("Maintenance guard: data changed since snapshot")
    return result
