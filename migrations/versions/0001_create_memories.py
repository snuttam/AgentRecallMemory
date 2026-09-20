"""create memories table

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("importance_score", sa.Float, nullable=False),
        sa.Column("access_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    # ivfflat index deferred: flat scan is fine at Phase 1 volumes (see docs/PHASE_PLAN.md).


def downgrade() -> None:
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_table("memories")
