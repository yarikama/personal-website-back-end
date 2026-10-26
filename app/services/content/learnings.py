from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy import delete as delete_rows
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.learnings import LearningEdge, LearningNode
from db.rows import ConflictError, NotFoundError, ensure_unique, get_or_raise
from schemas.learnings import (
    GraphEdge,
    GraphNode,
    LearningGraph,
    LearningNodeCreate,
    LearningNodeUpdate,
)

WHAT = "Learning node"

# The advisory lock every new edge takes, for as long as its transaction.
LINK_LOCK = 0x4C4E4B53


async def graph(db: AsyncSession, *, published_only: bool) -> LearningGraph:
    """Every node (only published ones, or drafts too) by slug, and the
    edges between them: an edge to a draft is left out with it."""
    query = select(LearningNode).order_by(LearningNode.slug)
    if published_only:
        query = query.where(LearningNode.published.is_(True))
    nodes = (await db.scalars(query)).all()
    slugs = {node.id: node.slug for node in nodes}
    edges = sorted(
        (
            GraphEdge.model_validate(
                {
                    "source": slugs[edge.source_id],
                    "target": slugs[edge.target_id],
                    "type": edge.type,
                }
            )
            for edge in await db.scalars(select(LearningEdge))
            if edge.source_id in slugs and edge.target_id in slugs
        ),
        key=lambda edge: (edge.source, edge.target, edge.type),
    )
    return LearningGraph(
        nodes=[GraphNode.model_validate(node) for node in nodes], edges=edges
    )


async def links(db: AsyncSession, node: LearningNode) -> dict[str, list[str]]:
    """The slugs of the nodes `node` links to and from, drafts too, by what
    they are to it."""
    found: dict[str, list[str]] = {
        "prerequisites": [],
        "leads_to": [],
        "part_of": [],
        "parts": [],
        "related": [],
    }
    # (the type, whether the edge leads out of `node`) -> the list it goes in
    name = {
        ("prerequisite", False): "prerequisites",
        ("prerequisite", True): "leads_to",
        ("part_of", True): "part_of",
        ("part_of", False): "parts",
        ("related", True): "related",
        ("related", False): "related",
    }
    for out, this, other in (
        (True, LearningEdge.source_id, LearningEdge.target_id),
        (False, LearningEdge.target_id, LearningEdge.source_id),
    ):
        rows = await db.execute(
            select(LearningEdge.type, LearningNode.slug)
            .join(LearningNode, LearningNode.id == other)
            .where(this == node.id)
        )
        for type, slug in rows:
            found[name[type, out]].append(slug)
    return {key: sorted(slugs) for key, slugs in found.items()}


async def get(db: AsyncSession, id: UUID) -> LearningNode:
    return await get_or_raise(db, LearningNode, id, WHAT)


async def get_by_slug(db: AsyncSession, slug: str) -> LearningNode:
    node = await db.scalar(select(LearningNode).where(LearningNode.slug == slug))
    if node is None:
        raise NotFoundError(f"{WHAT} '{slug}' not found")
    return node


async def create(db: AsyncSession, fields: LearningNodeCreate) -> LearningNode:
    await ensure_unique(db, LearningNode.slug, fields.slug, WHAT)
    node = LearningNode(**fields.model_dump())
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return node


async def update(
    db: AsyncSession, id: UUID, fields: LearningNodeUpdate
) -> LearningNode:
    """Sets the fields `fields` was given, and no others."""
    node = await get(db, id)
    changes = fields.model_dump(exclude_unset=True)
    if "slug" in changes:
        await ensure_unique(db, LearningNode.slug, changes["slug"], WHAT, exclude_id=id)
    for field, value in changes.items():
        setattr(node, field, value)
    await db.commit()
    await db.refresh(node)
    return node


async def delete(db: AsyncSession, id: UUID) -> None:
    """Deletes the node and its edges."""
    await db.delete(await get(db, id))
    await db.commit()


async def reaches(db: AsyncSession, start: UUID, goal: UUID, type: str) -> bool:
    """Whether edges of `type` lead from `start` to `goal`, through any
    number of nodes."""
    reach = (
        select(LearningEdge.target_id)
        .where(LearningEdge.source_id == start, LearningEdge.type == type)
        .cte("reach", recursive=True)
    )
    # UNION, not UNION ALL: a node reached twice is not followed twice, so
    # the walk ends even where there is a cycle already.
    reach = reach.union(
        select(LearningEdge.target_id)
        .join(reach, LearningEdge.source_id == reach.c.target_id)
        .where(LearningEdge.type == type)
    )
    return bool(await db.scalar(select(func.count()).where(reach.c.target_id == goal)))


async def link(
    db: AsyncSession, source: LearningNode, target: LearningNode, type: str
) -> None:
    """A new edge. ConflictError for a node to itself, an edge there already
    is (a related one either way round), or one that would close a cycle of
    prerequisites or of parts."""
    if source.id == target.id:
        raise ConflictError("A node cannot link to itself")
    # One new edge at a time: two at once could each find no cycle, and
    # together make one.
    await db.execute(select(func.pg_advisory_xact_lock(LINK_LOCK)))
    if await db.get(LearningEdge, (source.id, target.id, type)) is not None:
        raise ConflictError(
            f"'{source.slug}' already links to '{target.slug}' as {type}"
        )
    if type == "related":
        if await db.get(LearningEdge, (target.id, source.id, type)) is not None:
            raise ConflictError(
                f"'{target.slug}' is already related to '{source.slug}'"
            )
    elif await reaches(db, target.id, source.id, type):
        raise ConflictError(
            f"That would make a cycle: '{target.slug}' already leads to "
            f"'{source.slug}' through {type} links"
        )
    db.add(LearningEdge(source_id=source.id, target_id=target.id, type=type))
    await db.commit()


async def unlink(
    db: AsyncSession, source: LearningNode, target: LearningNode, type: str
) -> None:
    """Deletes the edge (a related one either way round); NotFoundError if
    there is none."""
    one_way = and_(
        LearningEdge.source_id == source.id, LearningEdge.target_id == target.id
    )
    if type == "related":
        other_way = and_(
            LearningEdge.source_id == target.id, LearningEdge.target_id == source.id
        )
        between = or_(one_way, other_way)
    else:
        between = one_way
    removed = await db.scalar(
        delete_rows(LearningEdge)
        .where(between, LearningEdge.type == type)
        .returning(LearningEdge.type)
    )
    if removed is None:
        raise NotFoundError(f"No {type} link from '{source.slug}' to '{target.slug}'")
    await db.commit()
