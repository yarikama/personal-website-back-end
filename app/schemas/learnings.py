import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from schemas.base import BaseSchema

Kind = Literal["learning", "concept", "external"]
EdgeType = Literal["prerequisite", "part_of", "related", "next"]


class LearningNodeCreate(BaseSchema):
    slug: str
    kind: Kind
    title: str
    summary: str
    ref: str | None = None
    tags: list[str] = Field(default_factory=list)
    preview_image: str | None = None
    published: bool = False


class LearningNodeUpdate(BaseSchema):
    slug: str | None = None
    kind: Kind | None = None
    title: str | None = None
    summary: str | None = None
    ref: str | None = None
    tags: list[str] | None = None
    preview_image: str | None = None
    published: bool | None = None


class LearningNodeResponse(LearningNodeCreate):
    id: UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class GraphNode(BaseSchema):
    slug: str
    kind: Kind
    title: str
    summary: str
    ref: str | None
    tags: list[str]
    preview_image: str | None
    published: bool


class GraphEdge(BaseSchema):
    """An edge by its nodes' slugs."""

    source: str
    target: str
    type: EdgeType
    # A part_of edge that is the source's home: where it sits on the map
    home: bool = False


class LearningGraph(BaseSchema):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
