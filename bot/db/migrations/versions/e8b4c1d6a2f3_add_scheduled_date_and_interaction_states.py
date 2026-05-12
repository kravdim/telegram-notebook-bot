"""add scheduled date and interaction states

Revision ID: e8b4c1d6a2f3
Revises: d5a7b2c9e4f0
Create Date: 2026-05-08 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8b4c1d6a2f3"
down_revision: Union[str, None] = "d5a7b2c9e4f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("scheduled_date", sa.Date(), nullable=True))
    op.execute("UPDATE tasks SET scheduled_date = due_date WHERE due_date IS NOT NULL")
    op.create_index(
        "idx_tasks_scheduled_date",
        "tasks",
        ["scheduled_date"],
        unique=False,
        postgresql_where="status = 'open'",
    )

    op.create_table(
        "interaction_states",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("state_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default="now()", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default="now()", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "idx_interaction_states_expires",
        "interaction_states",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_interaction_states_expires", table_name="interaction_states")
    op.drop_table("interaction_states")
    op.drop_index(
        "idx_tasks_scheduled_date",
        table_name="tasks",
        postgresql_where="status = 'open'",
    )
    op.drop_column("tasks", "scheduled_date")
