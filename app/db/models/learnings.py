"""
The Learnings page's knowledge graph. A node is a learning page on the site
(its page is code in the front end; the node is its card and its place in
the graph), a concept that only gathers others, or a link elsewhere. Edges
are typed and point one way:

- prerequisite: the source is learned before the target
- part_of: the source is part of the target. A node can be part of several
  others; one of those edges is its home, where it sits on the map
- related: either way round; kept once
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base

KINDS = ("learning", "concept", "external")
EDGE_TYPES = ("prerequisite", "part_of", "related", "next")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class LearningNode(Base):
    __tablename__ = "learning_nodes"
    __table_args__ = (
        CheckConstraint(one_of("kind", KINDS), name="learning_nodes_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    # Where the node leads: the page's path on the site for a learning, a URL
    # for an external link, nothing for a concept.
    ref: Mapped[str | None] = mapped_column(String(2048))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    preview_image: Mapped[str | None] = mapped_column(String(500))
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class LearningEdge(Base):
    __tablename__ = "learning_edges"
    __table_args__ = (
        CheckConstraint(one_of("type", EDGE_TYPES), name="learning_edges_type"),
        CheckConstraint("source_id <> target_id", name="learning_edges_not_a_loop"),
        CheckConstraint(
            "NOT home OR type = 'part_of'", name="learning_edges_home_is_part_of"
        ),
        # The primary key starts with source_id; this finds what leads in.
        Index("ix_learning_edges_target_id", "target_id"),
        # At most one home per node
        Index(
            "ix_learning_edges_one_home",
            "source_id",
            unique=True,
            postgresql_where=text("home"),
        ),
        # The reading order is a line: one page next, one before
        Index(
            "ix_learning_edges_one_next",
            "source_id",
            unique=True,
            postgresql_where=text("type = 'next'"),
        ),
        Index(
            "ix_learning_edges_one_previous",
            "target_id",
            unique=True,
            postgresql_where=text("type = 'next'"),
        ),
    )

    # Deleting a node deletes its edges (in the database: there is no
    # relationship for the session to load).
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    type: Mapped[str] = mapped_column(String(20), primary_key=True)
    home: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
