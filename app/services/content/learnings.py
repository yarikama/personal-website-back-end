from typing import Any
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
                    "home": edge.home,
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


async def links(db: AsyncSession, node: LearningNode) -> dict[str, Any]:
    """The slugs of the nodes `node` links to and from, drafts too, by what
    they are to it, and the slugs of its home and of the nodes read next and
    before it (each None if there is none)."""
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
    home = None
    # In the reading order: there is at most one each way
    order: dict[bool, str | None] = {True: None, False: None}
    for out, this, other in (
        (True, LearningEdge.source_id, LearningEdge.target_id),
        (False, LearningEdge.target_id, LearningEdge.source_id),
    ):
        rows = await db.execute(
            select(LearningEdge.type, LearningEdge.home, LearningNode.slug)
            .join(LearningNode, LearningNode.id == other)
            .where(this == node.id)
        )
        for type, is_home, slug in rows:
            if type == "next":
                order[out] = slug
                continue
            found[name[type, out]].append(slug)
            if is_home and out:
                home = slug
    return {
        **{key: sorted(slugs) for key, slugs in found.items()},
        "home": home,
        "next": order[True],
        "previous": order[False],
    }


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


async def taken_next(
    db: AsyncSession, source: LearningNode, target: LearningNode
) -> str | None:
    """Why `source` can't lead on to `target` in the reading order: one of
    them is already linked that way to another node. None if it can."""
    after = await db.scalar(
        select(LearningNode.slug)
        .join(LearningEdge, LearningEdge.target_id == LearningNode.id)
        .where(LearningEdge.source_id == source.id, LearningEdge.type == "next")
    )
    if after is not None:
        return f"'{source.slug}' already leads on to '{after}': unlink that first"
    before = await db.scalar(
        select(LearningNode.slug)
        .join(LearningEdge, LearningEdge.source_id == LearningNode.id)
        .where(LearningEdge.target_id == target.id, LearningEdge.type == "next")
    )
    if before is not None:
        return f"'{before}' already leads on to '{target.slug}': unlink that first"
    return None


async def home_of(db: AsyncSession, node_id: UUID) -> LearningEdge | None:
    """The part_of edge that is the node's home, if it has one."""
    return await db.scalar(
        select(LearningEdge).where(
            LearningEdge.source_id == node_id,
            LearningEdge.type == "part_of",
            LearningEdge.home.is_(True),
        )
    )


async def link(
    db: AsyncSession,
    source: LearningNode,
    target: LearningNode,
    type: str,
    *,
    home: bool = False,
) -> LearningEdge:
    """A new edge. ConflictError for a node to itself, an edge there already
    is (a related one either way round), one that would close a cycle of
    prerequisites, of parts or of the reading order, or a second next from
    `source` or to `target` (the reading order is a line).

    A node's first part_of edge is its home; a later one becomes it only
    with `home`, taking over from the one before. `home` means nothing for
    the other types."""
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
    elif type == "next" and (taken := await taken_next(db, source, target)):
        raise ConflictError(taken)
    elif await reaches(db, target.id, source.id, type):
        raise ConflictError(
            f"That would make a cycle: '{target.slug}' already leads to "
            f"'{source.slug}' through {type} links"
        )
    edge = LearningEdge(source_id=source.id, target_id=target.id, type=type)
    if type == "part_of":
        current = await home_of(db, source.id)
        if home and current is not None:
            current.home = False
            # Before the new home goes in: a node has one home at a time
            await db.flush()
        edge.home = home or current is None
    db.add(edge)
    await db.commit()
    return edge


async def set_home(db: AsyncSession, node: LearningNode, parent: LearningNode) -> None:
    """Makes `node`'s part_of edge to `parent` its home. NotFoundError if
    `node` is not part of `parent`."""
    await db.execute(select(func.pg_advisory_xact_lock(LINK_LOCK)))
    edge = await db.get(LearningEdge, (node.id, parent.id, "part_of"))
    if edge is None:
        raise NotFoundError(f"'{node.slug}' is not part of '{parent.slug}'")
    current = await home_of(db, node.id)
    if current is not None and current is not edge:
        current.home = False
        await db.flush()
    edge.home = True
    await db.commit()


async def unlink(
    db: AsyncSession, source: LearningNode, target: LearningNode, type: str
) -> None:
    """Deletes the edge (a related one either way round); NotFoundError if
    there is none. A node that loses its home moves into the part_of edge
    it gained first."""
    await db.execute(select(func.pg_advisory_xact_lock(LINK_LOCK)))
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
        .returning(LearningEdge.home)
    )
    if removed is None:
        raise NotFoundError(f"No {type} link from '{source.slug}' to '{target.slug}'")
    if removed:
        following = await db.scalar(
            select(LearningEdge)
            .where(LearningEdge.source_id == source.id, LearningEdge.type == "part_of")
            .order_by(LearningEdge.created_at, LearningEdge.target_id)
            .limit(1)
        )
        if following is not None:
            following.home = True
    await db.commit()
