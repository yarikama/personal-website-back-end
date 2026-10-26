"""add learning_nodes and learning_edges

Revision ID: f1a2b3c4d5e6
Revises: e9b0c1d2e3f4
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """New tables only, so the running release is unaffected."""
    op.create_table(
        "learning_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("ref", sa.String(2048), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("preview_image", sa.String(500), nullable=True),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('learning', 'concept', 'external')", name="learning_nodes_kind"
        ),
    )
    op.create_table(
        "learning_edges",
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("type", sa.String(20), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('prerequisite', 'part_of', 'related')",
            name="learning_edges_type",
        ),
        sa.CheckConstraint("source_id <> target_id", name="learning_edges_not_a_loop"),
    )
    op.create_index("ix_learning_edges_target_id", "learning_edges", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_edges_target_id", "learning_edges")
    op.drop_table("learning_edges")
    op.drop_table("learning_nodes")
