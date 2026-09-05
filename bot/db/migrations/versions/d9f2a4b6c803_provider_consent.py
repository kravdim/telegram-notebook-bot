"""Bind consent to the recipients actually shown to the user."""

import sqlalchemy as sa
from alembic import op

revision = "d9f2a4b6c803"
down_revision = "c8e1f3a5b702"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No backfill: old consent did not identify the current recipient set.
    op.add_column("users", sa.Column("privacy_provider_fingerprint", sa.Text()))


def downgrade() -> None:
    # An older runtime cannot validate fingerprints. Do not silently widen consent.
    op.execute("UPDATE users SET cloud_processing_enabled = false, privacy_notice_version = 0")
    op.drop_column("users", "privacy_provider_fingerprint")
