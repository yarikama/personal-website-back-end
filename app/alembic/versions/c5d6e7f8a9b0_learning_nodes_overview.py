"""overview: a field or topic's introduction to what it gathers

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A nullable column the running release ignores."""
    op.add_column("learning_nodes", sa.Column("overview", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("learning_nodes", "overview")
