"""admin_todos: who added each (source), and the morning plan's bucket and reason

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Columns the running release ignores: `source` has a default, so its
    inserts still work."""
    op.add_column(
        "admin_todos",
        sa.Column(
            "source", sa.String(16), server_default="owner", nullable=False
        ),
    )
    op.add_column("admin_todos", sa.Column("bucket", sa.String(16), nullable=True))
    op.add_column("admin_todos", sa.Column("reason", sa.Text(), nullable=True))
    op.execute("UPDATE admin_todos SET source = 'daily' WHERE daily_key IS NOT NULL")


def downgrade() -> None:
    op.drop_column("admin_todos", "reason")
    op.drop_column("admin_todos", "bucket")
    op.drop_column("admin_todos", "source")
