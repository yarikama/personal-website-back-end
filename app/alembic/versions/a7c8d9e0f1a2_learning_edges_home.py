"""a node's home among the nodes it is part of

Revision ID: a7c8d9e0f1a2
Revises: f1a2b3c4d5e6
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A new column with a default, so the running release is unaffected.
    Every node already part of something gets its oldest such edge as home."""
    op.add_column(
        "learning_edges",
        sa.Column("home", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("learning_edges", "home", server_default=None)
    op.execute(
        """
        UPDATE learning_edges AS e SET home = true
        FROM (
            SELECT DISTINCT ON (source_id) source_id, target_id
            FROM learning_edges
            WHERE type = 'part_of'
            ORDER BY source_id, created_at, target_id
        ) AS first
        WHERE e.type = 'part_of'
          AND e.source_id = first.source_id
          AND e.target_id = first.target_id
        """
    )
    op.create_check_constraint(
        "learning_edges_home_is_part_of", "learning_edges", "NOT home OR type = 'part_of'"
    )
    op.create_index(
        "ix_learning_edges_one_home",
        "learning_edges",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("home"),
    )


def downgrade() -> None:
    op.drop_index("ix_learning_edges_one_home", "learning_edges")
    op.drop_constraint("learning_edges_home_is_part_of", "learning_edges")
    op.drop_column("learning_edges", "home")
