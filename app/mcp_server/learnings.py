"""
The Learnings page's knowledge graph: its nodes and the typed edges between
them. A learning node is the card of a page whose content is code in the
front end, so it is published only once that page is live. Drafts are
included everywhere, as for the notes.
"""

from typing import Annotated, Any
from uuid import UUID

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.learnings import LearningNode
from mcp_server.common import (
    OUTWARD,
    READ,
    WRITE,
    Slug,
    as_tool_errors,
    given,
    who,
)
from schemas.learnings import (
    EdgeType,
    Kind,
    LearningNodeCreate,
    LearningNodeResponse,
    LearningNodeUpdate,
)
from services.content import learnings

IdOrSlug = Annotated[str, Field(description="The node's id (a UUID) or its slug")]
KindField = Annotated[
    Kind,
    Field(
        description="learning: a page of its own on the site; concept: only "
        "gathers other nodes; external: a link elsewhere (a paper, a video)"
    ),
]
Ref = Annotated[
    str | None,
    Field(
        description="Where it leads. learning: the page's path on the site, "
        "/learnings/<slug> if omitted; external: an https:// URL; concept: none"
    ),
]
Summary = Annotated[
    str,
    Field(description="One to three sentences for its card and preview"),
]
Tags = Annotated[list[str], Field(description="A few tags; reuse existing ones")]
Type = Annotated[
    EdgeType,
    Field(
        description="prerequisite: source is learned before target; part_of: "
        "source is part of target; related: either way round"
    ),
]


def page_of(slug: str) -> str:
    return f"/learnings/{slug}"


def check_ref(kind: str, ref: str | None) -> None:
    """The ref a node of this kind may have."""
    if kind == "learning" and not (
        ref and ref.startswith("/") and not ref.startswith("//")
    ):
        raise ToolError("A learning's ref is its path on the site, like /learnings/x.")
    if kind == "external" and not (ref and ref.startswith("https://")):
        raise ToolError("An external node's ref is an https:// URL.")
    if kind == "concept" and ref is not None:
        raise ToolError("A concept leads nowhere: leave ref out.")


async def find(db: AsyncSession, id_or_slug: str) -> LearningNode:
    try:
        id = UUID(id_or_slug)
    except ValueError:
        return await learnings.get_by_slug(db, id_or_slug)
    return await learnings.get(db, id)


def full(node: LearningNode) -> dict[str, Any]:
    return LearningNodeResponse.model_validate(node).model_dump(mode="json")


def register(server: MCPServer, sessions: async_sessionmaker[AsyncSession]) -> None:
    @server.tool(annotations=READ)
    async def get_learning_graph(
        include_drafts: Annotated[
            bool, Field(description="false: only what the site shows")
        ] = True,
    ) -> dict[str, Any]:
        """The whole graph: every node and the edges between them, by slug.
        Read it before adding nodes, to link to what is there."""
        async with sessions() as db:
            graph = await learnings.graph(db, published_only=not include_drafts)
        return graph.model_dump(mode="json")

    @server.tool(annotations=READ)
    async def get_learning_node(id_or_slug: IdOrSlug) -> dict[str, Any]:
        """One node, draft or not, and the slugs it links to and from."""
        async with sessions() as db:
            with as_tool_errors():
                node = await find(db, id_or_slug)
                return {**full(node), "links": await learnings.links(db, node)}

    @server.tool(annotations=WRITE)
    async def create_learning_node(
        slug: Slug,
        kind: KindField,
        title: str,
        summary: Summary,
        ref: Ref = None,
        tags: Tags | None = None,
        preview_image: Annotated[
            str | None, Field(description="An image's URL on the site")
        ] = None,
        published: Annotated[
            bool, Field(description="Leave false unless asked to publish")
        ] = False,
    ) -> dict[str, Any]:
        """Adds a node, a draft unless `published`. Link it with
        link_learning_nodes."""
        if kind == "learning" and ref is None:
            ref = page_of(slug)
        check_ref(kind, ref)
        fields = LearningNodeCreate(
            slug=slug,
            kind=kind,
            title=title,
            summary=summary,
            ref=ref,
            tags=tags or [],
            preview_image=preview_image,
            published=published,
        )
        async with sessions() as db:
            with as_tool_errors():
                node = await learnings.create(db, fields)
        logger.info(f"MCP create_learning_node by {who()}: {node.slug}")
        return full(node)

    @server.tool(annotations=WRITE)
    async def update_learning_node(
        id_or_slug: IdOrSlug,
        slug: Slug | None = None,
        kind: KindField | None = None,
        title: str | None = None,
        summary: Summary | None = None,
        ref: Ref = None,
        tags: Tags | None = None,
        preview_image: str | None = None,
    ) -> dict[str, Any]:
        """Changes the fields given and leaves the rest. A learning still at
        /learnings/<slug> moves with a new slug; a concept loses its ref.
        Publishing is set_learning_node_published."""
        changes = given(
            slug=slug,
            kind=kind,
            title=title,
            summary=summary,
            ref=ref,
            tags=tags,
            preview_image=preview_image,
        )
        if not changes:
            raise ToolError("Nothing to change: give at least one field.")
        async with sessions() as db:
            with as_tool_errors():
                node = await find(db, id_or_slug)
                new_kind = kind or node.kind
                if ref is None:
                    if new_kind == "concept":
                        changes["ref"] = None
                    elif new_kind == "learning" and (
                        node.kind != "learning" or node.ref == page_of(node.slug)
                    ):
                        changes["ref"] = page_of(slug or node.slug)
                check_ref(new_kind, changes.get("ref", node.ref))
                node = await learnings.update(
                    db, node.id, LearningNodeUpdate(**changes)
                )
        logger.info(
            f"MCP update_learning_node by {who()}: {node.slug} {sorted(changes)}"
        )
        return full(node)

    @server.tool(annotations=OUTWARD)
    async def set_learning_node_published(
        id_or_slug: IdOrSlug, published: bool
    ) -> dict[str, Any]:
        """Shows a node on the Learnings page (within a minute, the edge
        cache's time) or hides it again. Only when the owner asks, and for a
        learning only once its page is live."""
        async with sessions() as db:
            with as_tool_errors():
                node = await find(db, id_or_slug)
                node = await learnings.update(
                    db, node.id, LearningNodeUpdate(published=published)
                )
        action = "published" if published else "unpublished"
        logger.info(f"MCP {action} a learning node, by {who()}: {node.slug}")
        return full(node)

    @server.tool(annotations=OUTWARD)
    async def delete_learning_node(
        id_or_slug: IdOrSlug,
        confirm_slug: Annotated[
            str, Field(description="The node's slug again, as a check")
        ],
    ) -> str:
        """Deletes a node and its links for good. Only when the owner asks;
        set_learning_node_published hides it instead."""
        async with sessions() as db:
            with as_tool_errors():
                node = await find(db, id_or_slug)
                if confirm_slug != node.slug:
                    raise ToolError(
                        f"confirm_slug does not match: the node's slug is "
                        f"'{node.slug}'. Nothing was deleted."
                    )
                await learnings.delete(db, node.id)
        logger.info(f"MCP delete_learning_node by {who()}: {node.slug}")
        return f"Deleted '{node.title}' ({node.slug}) and its links."

    @server.tool(annotations=WRITE)
    async def link_learning_nodes(
        source: IdOrSlug, target: IdOrSlug, type: Type
    ) -> dict[str, str]:
        """Links two nodes. A cycle of prerequisites or of parts is refused,
        as is a link there already is."""
        async with sessions() as db:
            with as_tool_errors():
                start = await find(db, source)
                end = await find(db, target)
                await learnings.link(db, start, end, type)
        logger.info(
            f"MCP link_learning_nodes by {who()}: {start.slug} {type} {end.slug}"
        )
        return {"source": start.slug, "target": end.slug, "type": type}

    @server.tool(annotations=WRITE)
    async def unlink_learning_nodes(
        source: IdOrSlug, target: IdOrSlug, type: Type
    ) -> str:
        """Removes a link (a related one, given either way round)."""
        async with sessions() as db:
            with as_tool_errors():
                start = await find(db, source)
                end = await find(db, target)
                await learnings.unlink(db, start, end, type)
        logger.info(
            f"MCP unlink_learning_nodes by {who()}: {start.slug} {type} {end.slug}"
        )
        return f"Removed the {type} link from '{start.slug}' to '{end.slug}'."
