import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from schemas.base import BaseSchema

Kind = Literal["learning", "concept", "external"]
EdgeType = Literal["prerequisite", "part_of", "related", "next"]


# A learning's title and summary are its page's search title and description,
# and search results cut a title past about 60 characters and a description
# past 160, so a node written longer is refused.
TITLE_MAX = 60
SUMMARY_MAX = 160


class LearningNodeFields(BaseSchema):
    slug: str
    kind: Kind
    title: str
    summary: str
    overview: str | None = None
    ref: str | None = None
    tags: list[str] = Field(default_factory=list)
    preview_image: str | None = None
    published: bool = False


class LearningNodeCreate(LearningNodeFields):
    title: str = Field(max_length=TITLE_MAX)
    summary: str = Field(max_length=SUMMARY_MAX)


class LearningNodeUpdate(BaseSchema):
    slug: str | None = None
    kind: Kind | None = None
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    summary: str | None = Field(default=None, max_length=SUMMARY_MAX)
    overview: str | None = None
    ref: str | None = None
    tags: list[str] | None = None
    preview_image: str | None = None
    published: bool | None = None


# Not held to the limits: a node written before them must still be read
class LearningNodeResponse(LearningNodeFields):
    id: UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class GraphNode(BaseSchema):
    slug: str
    kind: Kind
    title: str
    summary: str
    overview: str | None
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
