"""Durable reminder ownership, retry scheduling and calendar timezone.

Revision ID: b7d0e2f4a601
Revises: a6c9d1e4f7b2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7d0e2f4a601"
down_revision = "a6c9d1e4f7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reminders", sa.Column("series_timezone", sa.Text(), nullable=False,
                                         server_default="Europe/Moscow"))
    op.add_column("reminders", sa.Column("lease_token", postgresql.UUID(as_uuid=True)))
    op.add_column("reminders", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("reminders", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE reminders r SET series_timezone = u.timezone FROM users u "
               "WHERE r.user_id = u.telegram_id")
    op.drop_constraint("ck_reminders_status", "reminders", type_="check")
    op.create_check_constraint("ck_reminders_status", "reminders",
                               "status IN ('pending', 'delivered', 'snoozed', 'resolved', "
                               "'cancelled', 'failed')")


def downgrade() -> None:
    # Do not silently convert delivery failures into user cancellation or replay them.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM reminders WHERE status='failed')")):
        raise RuntimeError("Resolve failed reminders before downgrading the delivery schema")
    op.drop_constraint("ck_reminders_status", "reminders", type_="check")
    op.create_check_constraint("ck_reminders_status", "reminders",
                               "status IN ('pending', 'delivered', 'snoozed', 'resolved', 'cancelled')")
    for name in ("next_attempt_at", "lease_expires_at", "lease_token", "series_timezone"):
        op.drop_column("reminders", name)
