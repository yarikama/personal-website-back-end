"""add mcp_clients and mcp_refresh_tokens

Revision ID: e9b0c1d2e3f4
Revises: 68af34127b85
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "68af34127b85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """New tables only, so the running release is unaffected."""
    op.create_table(
        "mcp_clients",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("info", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "mcp_refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(64),
            sa.ForeignKey("mcp_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("resource", sa.String(2048), nullable=True),
        sa.Column("access_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_mcp_refresh_tokens_client_id", "mcp_refresh_tokens", ["client_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_refresh_tokens_client_id", "mcp_refresh_tokens")
    op.drop_table("mcp_refresh_tokens")
    op.drop_table("mcp_clients")
