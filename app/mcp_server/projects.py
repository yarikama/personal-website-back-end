"""
The works page: projects and the categories they are filed under. Like the
notes, unpublished projects are included everywhere, and publishing or
deleting is a tool of its own.
"""

from typing import Annotated, Any
from uuid import UUID

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.category import Category
from db.models.projects import Project
from mcp_server.common import (
    OUTWARD,
    READ,
    WRITE,
    Slug,
    as_tool_errors,
    given,
    who,
)
from schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)
from schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from services.content import categories, projects

IdOrSlug = Annotated[str, Field(description="The project's id (a UUID) or its slug")]
NameOrId = Annotated[
    str, Field(description="The category's name (e.g. 'ml') or its id (a UUID)")
]
Name = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=50,
        description="Lowercase kebab-case: the filter's id on the works page",
    ),
]


async def find(db: AsyncSession, id_or_slug: str) -> Project:
    try:
        id = UUID(id_or_slug)
    except ValueError:
        return await projects.get_by_slug(db, id_or_slug)
    return await projects.get(db, id)


def not_reserved(name: str | None) -> None:
    # The works page's "All" filter (categories.with_counts) is named "all".
    if name == "all":
        raise ToolError("'all' is the works page's own filter; pick another name.")


async def find_category(db: AsyncSession, name_or_id: str) -> Category:
    try:
        id = UUID(name_or_id)
    except ValueError:
        return await categories.get_by_name(db, name_or_id)
    return await categories.get(db, id)


def full(project: Project) -> dict[str, Any]:
    return ProjectResponse.model_validate(project).model_dump(mode="json")


def plain(category: Category) -> dict[str, Any]:
    return CategoryResponse.model_validate(category).model_dump(mode="json")


def register(server: MCPServer, sessions: async_sessionmaker[AsyncSession]) -> None:
    # Projects ---------------------------------------------------------------

    @server.tool(annotations=READ)
    async def list_projects(
        published: Annotated[
            bool | None,
            Field(
                description="true: published only; false: unpublished only; "
                "omit for both"
            ),
        ] = None,
        category: Annotated[
            str | None, Field(description="Only this category's (name or id)")
        ] = None,
        featured: bool | None = None,
        tag: Annotated[
            str | None, Field(description="Only projects with this tag")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The projects, in the works page's order, unpublished ones
        included, each with its category."""
        async with sessions() as db:
            with as_tool_errors():
                category_id = (
                    (await find_category(db, category)).id if category else None
                )
            page = await projects.page(
                db,
                published=published,
                category_id=category_id,
                featured=featured,
                tag=tag,
                limit=limit,
                offset=offset,
            )
        return {
            "projects": [project.model_dump(mode="json") for project in page["data"]],
            "pagination": page["pagination"],
        }

    @server.tool(annotations=READ)
    async def get_project(id_or_slug: IdOrSlug) -> dict[str, Any]:
        """One project, published or not."""
        async with sessions() as db:
            with as_tool_errors():
                return full(await find(db, id_or_slug))

    @server.tool(annotations=WRITE)
    async def create_project(
        title: str,
        slug: Slug,
        description: Annotated[str, Field(description="What it is and did")],
        category: Annotated[str, Field(description="Its category's name or id")],
        tags: list[str],
        year: Annotated[str, Field(description="e.g. '2025' or '2024-2025'")],
        cover_image: Annotated[
            str | None, Field(description="An image URL, uploaded first")
        ] = None,
        link: str | None = None,
        github: str | None = None,
        metrics: Annotated[
            str | None,
            Field(
                description="Its results in a line, joined by ' · ', e.g. "
                "'10× less compute · 0.43 ms per image'"
            ),
        ] = None,
        formula: str | None = None,
        featured: bool = False,
        order: Annotated[
            int, Field(description="Lower comes first on the works page")
        ] = 0,
        published: Annotated[
            bool, Field(description="Leave false unless asked to publish")
        ] = False,
    ) -> dict[str, Any]:
        """Creates a project, unpublished unless `published`."""
        async with sessions() as db:
            with as_tool_errors():
                category_id = (await find_category(db, category)).id
                project = await projects.create(
                    db,
                    ProjectCreate(
                        title=title,
                        slug=slug,
                        description=description,
                        category_id=category_id,
                        tags=tags,
                        year=year,
                        cover_image=cover_image,
                        link=link,
                        github=github,
                        metrics=metrics,
                        formula=formula,
                        featured=featured,
                        order=order,
                        published=published,
                    ),
                )
        logger.info(f"MCP create_project by {who()}: {project.slug}")
        return full(project)

    @server.tool(annotations=WRITE)
    async def update_project(
        id_or_slug: IdOrSlug,
        title: str | None = None,
        slug: Slug | None = None,
        description: str | None = None,
        category: Annotated[
            str | None, Field(description="A new category, by name or id")
        ] = None,
        tags: list[str] | None = None,
        year: str | None = None,
        cover_image: str | None = None,
        link: str | None = None,
        github: str | None = None,
        metrics: str | None = None,
        formula: str | None = None,
        featured: bool | None = None,
        order: int | None = None,
    ) -> dict[str, Any]:
        """Changes the fields given and leaves the rest. Publishing is
        set_project_published."""
        changes = given(
            title=title,
            slug=slug,
            description=description,
            tags=tags,
            year=year,
            cover_image=cover_image,
            link=link,
            github=github,
            metrics=metrics,
            formula=formula,
            featured=featured,
            order=order,
        )
        if not changes and category is None:
            raise ToolError("Nothing to change: give at least one field.")
        async with sessions() as db:
            with as_tool_errors():
                if category is not None:
                    changes["category_id"] = (await find_category(db, category)).id
                project = await find(db, id_or_slug)
                project = await projects.update(
                    db, project.id, ProjectUpdate(**changes)
                )
        logger.info(f"MCP update_project by {who()}: {project.slug} {sorted(changes)}")
        return full(project)

    @server.tool(annotations=OUTWARD)
    async def set_project_published(
        id_or_slug: IdOrSlug, published: bool
    ) -> dict[str, Any]:
        """Shows a project on the works page (within a minute, the edge
        cache's time) or hides it. Only when the owner asks."""
        async with sessions() as db:
            with as_tool_errors():
                project = await find(db, id_or_slug)
                # Only `published` is set. Not ProjectUpdate(published=...):
                # category_id's default is not spelled default=, which type
                # checkers take for a required argument.
                changes = ProjectUpdate.model_validate({"published": published})
                project = await projects.update(db, project.id, changes)
        action = "published" if published else "unpublished"
        logger.info(f"MCP {action} a project, by {who()}: {project.slug}")
        return full(project)

    @server.tool(annotations=OUTWARD)
    async def delete_project(
        id_or_slug: IdOrSlug,
        confirm_slug: Annotated[
            str, Field(description="The project's slug again, as a check")
        ],
    ) -> str:
        """Deletes a project for good (its cover image stays in storage).
        Only when the owner asks; set_project_published hides it instead."""
        async with sessions() as db:
            with as_tool_errors():
                project = await find(db, id_or_slug)
                if confirm_slug != project.slug:
                    raise ToolError(
                        f"confirm_slug does not match: the project's slug is "
                        f"'{project.slug}'. Nothing was deleted."
                    )
                await projects.delete(db, project.id)
        logger.info(f"MCP delete_project by {who()}: {project.slug}")
        return f"Deleted '{project.title}' ({project.slug})."

    # Categories -------------------------------------------------------------

    @server.tool(annotations=READ)
    async def list_categories() -> list[dict[str, Any]]:
        """Every category, in the works page's order."""
        async with sessions() as db:
            return [plain(c) for c in await categories.all_categories(db)]

    @server.tool(annotations=WRITE)
    async def create_category(
        name: Name,
        label: Annotated[
            str, Field(max_length=100, description="As shown, e.g. 'ML/AI'")
        ],
        description: str | None = None,
        order: Annotated[
            int, Field(ge=0, description="Lower comes first on the works page")
        ] = 0,
    ) -> dict[str, Any]:
        """Creates a category, which shows as a filter on the works page once
        a published project is in it."""
        not_reserved(name)
        fields = CategoryCreate(
            name=name, label=label, description=description, order=order
        )
        async with sessions() as db:
            with as_tool_errors():
                category = await categories.create(db, fields)
        logger.info(f"MCP create_category by {who()}: {category.name}")
        return plain(category)

    @server.tool(annotations=WRITE)
    async def update_category(
        name_or_id: NameOrId,
        name: Name | None = None,
        label: Annotated[str | None, Field(max_length=100)] = None,
        description: str | None = None,
        order: Annotated[int | None, Field(ge=0)] = None,
    ) -> dict[str, Any]:
        """Changes the fields given and leaves the rest."""
        changes = given(name=name, label=label, description=description, order=order)
        not_reserved(name)
        if not changes:
            raise ToolError("Nothing to change: give at least one field.")
        async with sessions() as db:
            with as_tool_errors():
                category = await find_category(db, name_or_id)
                category = await categories.update(
                    db, category.id, CategoryUpdate(**changes)
                )
        logger.info(f"MCP update_category by {who()}: {category.name}")
        return plain(category)

    @server.tool(annotations=OUTWARD)
    async def delete_category(
        name_or_id: NameOrId,
        confirm_name: Annotated[
            str, Field(description="The category's name again, as a check")
        ],
    ) -> str:
        """Deletes a category with no project in it. Only when the owner
        asks."""
        async with sessions() as db:
            with as_tool_errors():
                category = await find_category(db, name_or_id)
                if confirm_name != category.name:
                    raise ToolError(
                        f"confirm_name does not match: the category's name is "
                        f"'{category.name}'. Nothing was deleted."
                    )
                await categories.delete(db, category.id)
        logger.info(f"MCP delete_category by {who()}: {category.name}")
        return f"Deleted the category '{category.label}' ({category.name})."
