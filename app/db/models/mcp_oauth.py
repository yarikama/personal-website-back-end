from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class McpClient(Base):
    """
    An MCP client (claude.ai, a Claude Code install) as it registered itself
    (RFC 7591), kept as the SDK's OAuthClientInformationFull. In the database
    rather than Redis, which keeps nothing across restarts: claude.ai holds
    on to its client_id and would have to be reconnected by hand.
    """

    __tablename__ = "mcp_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    info: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class McpRefreshToken(Base):
    """A refresh token, by its SHA-256 only, with the access token issued
    alongside it so that revoking either ends both."""

    __tablename__ = "mcp_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("mcp_clients.client_id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String))
    resource: Mapped[str | None] = mapped_column(String(2048))
    access_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
