"""Persist command plans and per-action results with domain effects."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c8e1f3a5b702"
down_revision = "b7d0e2f4a601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("processed_requests", sa.Column("action_plan", JSONB()))
    op.add_column("processed_requests", sa.Column("action_results", JSONB(),
                                                  nullable=False, server_default="{}"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM processed_requests "
        "WHERE action_plan IS NOT NULL AND status != 'completed')"
    )):
        raise RuntimeError("Finish pending action plans before removing their recovery journal")
    op.drop_column("processed_requests", "action_results")
    op.drop_column("processed_requests", "action_plan")
