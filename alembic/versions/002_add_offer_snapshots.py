"""add offer_snapshots table

Revision ID: 002
Revises: 001
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offer_snapshots",
        sa.Column("offer_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), server_default="", nullable=False),
        sa.Column("url", sa.String(2048), server_default="", nullable=False),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("offer_id"),
    )


def downgrade() -> None:
    op.drop_table("offer_snapshots")
