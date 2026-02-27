"""add manual_cars table

Revision ID: 001
Revises:
Create Date: 2026-02-27
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_cars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("year", sa.String(32), nullable=False),
        sa.Column("mileage", sa.String(64), nullable=False),
        sa.Column("fuel", sa.String(128), server_default="", nullable=False),
        sa.Column("engine", sa.String(128), server_default="", nullable=False),
        sa.Column("transmission", sa.String(128), server_default="", nullable=False),
        sa.Column("price", sa.String(128), server_default="", nullable=False),
        sa.Column("auction_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("manual_cars")
