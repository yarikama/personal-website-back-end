"""drop request_logs

Revision ID: 68af34127b85
Revises: d8a9b0c1d2e3
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "68af34127b85"
down_revision: str | Sequence[str] | None = "d8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The ML predictor's request log. Nothing has written or read it since
    the predictor was removed, and no release uses the table, so the running
    one is unaffected."""
    op.drop_index(op.f("ix_request_logs_id"), table_name="request_logs")
    op.drop_table("request_logs")


def downgrade() -> None:
    """The table as 3a91f5925050 created it, empty."""
    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_request_logs_id"), "request_logs", ["id"], unique=False)
