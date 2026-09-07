"""Версии индекса, timezone серии и ограниченное восстановление outbox."""

import sqlalchemy as sa
from alembic import op

revision = "e0a3b5c7d914"
down_revision = "d9f2a4b6c803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("recurrence_timezone", sa.Text()))
    op.execute("""UPDATE tasks SET recurrence_timezone = users.timezone
                  FROM users WHERE tasks.user_id = users.telegram_id
                  AND tasks.repeat_rule IS NOT NULL""")
    for table in ("notes", "diary_entries", "memoir_entries", "knowledge_base"):
        # Unknown provenance remains NULL; text search works during reindexing.
        op.add_column(table, sa.Column("embedding_model", sa.Text()))
    op.add_column("delivery_batches", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("delivery_batches", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE delivery_batches SET expires_at = created_at + INTERVAL '24 hours'")
    op.drop_constraint("ck_delivery_batches_status", "delivery_batches", type_="check")
    op.create_check_constraint("ck_delivery_batches_status", "delivery_batches",
                               "status IN ('pending','delivering','delivered','failed','expired')")


def downgrade() -> None:
    # Terminal deliveries must not be resurrected by a code/schema rollback.
    op.execute("UPDATE delivery_batches SET status='delivered' WHERE status IN ('failed','expired')")
    op.drop_constraint("ck_delivery_batches_status", "delivery_batches", type_="check")
    op.create_check_constraint("ck_delivery_batches_status", "delivery_batches",
                               "status IN ('pending','delivering','delivered')")
    op.drop_column("delivery_batches", "expires_at")
    op.drop_column("delivery_batches", "next_attempt_at")
    for table in ("notes", "diary_entries", "memoir_entries", "knowledge_base"):
        op.drop_column(table, "embedding_model")
    op.drop_column("tasks", "recurrence_timezone")
