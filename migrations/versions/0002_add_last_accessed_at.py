"""add last_accessed_at for decay job

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("memories", "last_accessed_at")
