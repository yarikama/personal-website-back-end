"""next edges: the reading order of the Learnings pages

Revision ID: b4c5d6e7f8a9
Revises: a7c8d9e0f1a2
Create Date: 2026-10-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A new edge type, which the running release never writes, and the
    rule that the reading order is a line: one next, one before."""
    op.drop_constraint("learning_edges_type", "learning_edges")
    op.create_check_constraint(
        "learning_edges_type",
        "learning_edges",
        "type IN ('prerequisite', 'part_of', 'related', 'next')",
    )
    op.create_index(
        "ix_learning_edges_one_next",
        "learning_edges",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("type = 'next'"),
    )
    op.create_index(
        "ix_learning_edges_one_previous",
        "learning_edges",
        ["target_id"],
        unique=True,
        postgresql_where=sa.text("type = 'next'"),
    )


def downgrade() -> None:
    op.drop_index("ix_learning_edges_one_previous", "learning_edges")
    op.drop_index("ix_learning_edges_one_next", "learning_edges")
    op.execute("DELETE FROM learning_edges WHERE type = 'next'")
    op.drop_constraint("learning_edges_type", "learning_edges")
    op.create_check_constraint(
        "learning_edges_type",
        "learning_edges",
        "type IN ('prerequisite', 'part_of', 'related')",
    )
