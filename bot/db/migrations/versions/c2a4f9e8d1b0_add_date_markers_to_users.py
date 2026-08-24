"""add date markers to users

Revision ID: c2a4f9e8d1b0
Revises: b7e9a2c4d6f1
Create Date: 2026-05-04 00:00:01.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2a4f9e8d1b0"
down_revision: Union[str, None] = "b7e9a2c4d6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("digest_evening_sent_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("tasks_reminder_last_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tasks_reminder_last_date")
    op.drop_column("users", "digest_evening_sent_date")
