"""Add privacy consent and critical domain constraints.

Revision ID: a6c9d1e4f7b2
Revises: f4b8c2d6e1a0
Create Date: 2026-08-27 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c9d1e4f7b2"
down_revision: str | None = "f4b8c2d6e1a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "privacy_notice_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_privacy_notice_version",
        "users",
        "privacy_notice_version >= 0",
    )
    op.create_check_constraint(
        "ck_users_chronometry_interval_positive",
        "users",
        "chronometry_interval_min > 0",
    )
    op.create_check_constraint(
        "ck_users_work_time_order",
        "users",
        "work_start_time < work_end_time",
    )
    op.create_check_constraint(
        "ck_users_work_days",
        "users",
        "cardinality(work_days) > 0 "
        "AND work_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::integer[]",
    )
    op.create_check_constraint(
        "ck_trips_date_order", "trips", "end_date >= start_date"
    )
    op.create_check_constraint(
        "ck_tasks_remind_before_nonnegative",
        "tasks",
        "remind_before_min IS NULL OR remind_before_min >= 0",
    )
    op.create_check_constraint("ck_tasks_version_positive", "tasks", "version > 0")
    op.create_check_constraint(
        "ck_time_tracking_duration_positive",
        "time_tracking_entries",
        "duration_minutes > 0",
    )
    op.create_check_constraint(
        "ck_reminders_snooze_count", "reminders", "snooze_count >= 0"
    )
    op.create_check_constraint(
        "ck_reminders_delivery_attempts",
        "reminders",
        "delivery_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_delivery_batches_attempts", "delivery_batches", "attempts >= 0"
    )
    op.create_check_constraint(
        "ck_delivery_parts_position", "delivery_parts", "position >= 0"
    )
    op.create_check_constraint(
        "ck_delivery_parts_attempts", "delivery_parts", "attempts >= 0"
    )
    op.add_column(
        "users",
        sa.Column(
            "cloud_processing_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_constraint("ck_delivery_parts_attempts", "delivery_parts", type_="check")
    op.drop_constraint("ck_delivery_parts_position", "delivery_parts", type_="check")
    op.drop_constraint(
        "ck_delivery_batches_attempts", "delivery_batches", type_="check"
    )
    op.drop_constraint(
        "ck_reminders_delivery_attempts", "reminders", type_="check"
    )
    op.drop_constraint("ck_reminders_snooze_count", "reminders", type_="check")
    op.drop_constraint(
        "ck_time_tracking_duration_positive",
        "time_tracking_entries",
        type_="check",
    )
    op.drop_constraint("ck_tasks_version_positive", "tasks", type_="check")
    op.drop_constraint(
        "ck_tasks_remind_before_nonnegative", "tasks", type_="check"
    )
    op.drop_constraint("ck_trips_date_order", "trips", type_="check")
    op.drop_constraint("ck_users_work_days", "users", type_="check")
    op.drop_constraint("ck_users_work_time_order", "users", type_="check")
    op.drop_constraint(
        "ck_users_chronometry_interval_positive", "users", type_="check"
    )
    op.drop_constraint(
        "ck_users_privacy_notice_version", "users", type_="check"
    )
    op.drop_column("users", "cloud_processing_enabled")
    op.drop_column("users", "privacy_notice_version")
