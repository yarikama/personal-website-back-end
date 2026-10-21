"""
The lab notes, to read and to write: tools, the writing guide and each
note as a resource, and prompts to draft or review one. Drafts are included
everywhere; only the owner gets in (services/mcp_oauth.py).
"""

import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.lab_notes import LabNote
from db.rows import NotFoundError
from mcp_server.common import (
    OUTWARD,
    READ,
    WRITE,
    Slug,
    as_tool_errors,
    given,
    today,
    who,
)
from schemas.lab_notes import LabNoteCreate, LabNoteResponse, LabNoteUpdate
from services.content import lab_notes

GUIDE = Path(__file__).resolve().parent.parent / "content" / "writing-guide.md"

IdOrSlug = Annotated[str, Field(description="The note's id (a UUID) or its slug")]
Tags = Annotated[list[str], Field(description="2 to 5 tags; reuse existing ones")]


async def find(db: AsyncSession, id_or_slug: str) -> LabNote:
    try:
        id = UUID(id_or_slug)
    except ValueError:
        return await lab_notes.get_by_slug(db, id_or_slug)
    return await lab_notes.get(db, id)


def full(note: LabNote) -> dict[str, Any]:
    return LabNoteResponse.model_validate(note).model_dump(mode="json")


def register(server: MCPServer, sessions: async_sessionmaker[AsyncSession]) -> None:
    @server.tool(annotations=READ)
    async def list_lab_notes(
        published: Annotated[
            bool | None,
            Field(
                description="true: published only; false: drafts only; omit for both"
            ),
        ] = None,
        tag: Annotated[
            str | None, Field(description="Only notes with this tag")
        ] = None,
        search: Annotated[
            str | None,
            Field(description="Text in the title, excerpt or body, any case"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The lab notes, newest first, drafts included: title, slug,
        excerpt, tags, date and whether published, without the body."""
        async with sessions() as db:
            page = await lab_notes.page(
                db,
                published=published,
                tag=tag,
                search=search,
                limit=limit,
                offset=offset,
            )
        return {
            "notes": [note.model_dump(mode="json") for note in page["data"]],
            "pagination": page["pagination"],
        }

    @server.tool(annotations=READ)
    async def get_lab_note(id_or_slug: IdOrSlug) -> dict[str, Any]:
        """One lab note, draft or not, with its markdown body."""
        async with sessions() as db:
            with as_tool_errors():
                return full(await find(db, id_or_slug))

    @server.tool(annotations=READ)
    async def list_lab_note_tags() -> list[dict[str, Any]]:
        """Every tag in use, drafts included, with how many notes have it,
        most used first. Reuse these before making up a new one."""
        async with sessions() as db:
            return await lab_notes.tags(db, published_only=False)

    @server.tool(annotations=WRITE)
    async def create_lab_note(
        title: str,
        slug: Slug,
        excerpt: Annotated[
            str, Field(description="One or two sentences, about 170-260 characters")
        ],
        content: Annotated[str, Field(description="The body in Markdown")],
        tags: Tags,
        date: Annotated[
            datetime.date | None, Field(description="Defaults to today (UTC)")
        ] = None,
        published: Annotated[
            bool, Field(description="Leave false unless asked to publish")
        ] = False,
    ) -> dict[str, Any]:
        """Creates a lab note, a draft unless `published`. Its read time is
        worked out from the body."""
        fields = LabNoteCreate(
            title=title,
            slug=slug,
            excerpt=excerpt,
            content=content,
            tags=tags,
            read_time=lab_notes.read_time(content),
            date=date or today(),
            published=published,
        )
        async with sessions() as db:
            with as_tool_errors():
                note = await lab_notes.create(db, fields)
        logger.info(f"MCP create_lab_note by {who()}: {note.slug}")
        return full(note)

    @server.tool(annotations=WRITE)
    async def update_lab_note(
        id_or_slug: IdOrSlug,
        title: str | None = None,
        slug: Slug | None = None,
        excerpt: str | None = None,
        content: Annotated[
            str | None,
            Field(
                description="The whole new body; for a few sentences, use "
                "edit_lab_note_content"
            ),
        ] = None,
        tags: Tags | None = None,
        date: datetime.date | None = None,
    ) -> dict[str, Any]:
        """Changes the fields given and leaves the rest. Publishing is
        set_lab_note_published."""
        changes = given(
            title=title,
            slug=slug,
            excerpt=excerpt,
            content=content,
            tags=tags,
            date=date,
        )
        if not changes:
            raise ToolError("Nothing to change: give at least one field.")
        if content is not None:
            changes["read_time"] = lab_notes.read_time(content)
        async with sessions() as db:
            with as_tool_errors():
                note = await find(db, id_or_slug)
                note = await lab_notes.update(db, note.id, LabNoteUpdate(**changes))
        logger.info(f"MCP update_lab_note by {who()}: {note.slug} {sorted(changes)}")
        return full(note)

    @server.tool(annotations=WRITE)
    async def edit_lab_note_content(
        id_or_slug: IdOrSlug,
        old_text: Annotated[
            str,
            Field(
                description="Text in the body, exactly, appearing only once: "
                "include enough around it"
            ),
        ],
        new_text: Annotated[str, Field(description="What replaces it")],
    ) -> dict[str, Any]:
        """Replaces one passage of a note's body, without resending the
        whole of it. Returns the note."""
        if not old_text:
            raise ToolError("old_text is empty.")
        async with sessions() as db:
            with as_tool_errors():
                note = await find(db, id_or_slug)
                found = note.content.count(old_text)
                if found != 1:
                    raise ToolError(
                        "old_text is not in the body."
                        if found == 0
                        else f"old_text appears {found} times; include more of "
                        "the text around it so it appears once."
                    )
                content = note.content.replace(old_text, new_text, 1)
                note = await lab_notes.update(
                    db,
                    note.id,
                    LabNoteUpdate(
                        content=content, read_time=lab_notes.read_time(content)
                    ),
                )
        logger.info(f"MCP edit_lab_note_content by {who()}: {note.slug}")
        return full(note)

    @server.tool(annotations=OUTWARD)
    async def set_lab_note_published(
        id_or_slug: IdOrSlug,
        published: bool,
        keep_date: Annotated[
            bool,
            Field(
                description="Publishing dates the note today (UTC) unless this is true"
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Publishes a note on the site (within a minute, the edge cache's
        time) or takes it back to a draft. Only when the owner asks."""
        async with sessions() as db:
            with as_tool_errors():
                note = await find(db, id_or_slug)
                changes: dict[str, Any] = {"published": published}
                if published and not note.published and not keep_date:
                    changes["date"] = today()
                note = await lab_notes.update(db, note.id, LabNoteUpdate(**changes))
        action = "published" if published else "unpublished"
        logger.info(f"MCP {action} a lab note, by {who()}: {note.slug}")
        return full(note)

    @server.tool(annotations=OUTWARD)
    async def delete_lab_note(
        id_or_slug: IdOrSlug,
        confirm_slug: Annotated[
            str, Field(description="The note's slug again, as a check")
        ],
    ) -> str:
        """Deletes a note for good. Only when the owner asks; unpublishing
        (set_lab_note_published) keeps it as a draft instead."""
        async with sessions() as db:
            with as_tool_errors():
                note = await find(db, id_or_slug)
                if confirm_slug != note.slug:
                    raise ToolError(
                        f"confirm_slug does not match: the note's slug is "
                        f"'{note.slug}'. Nothing was deleted."
                    )
                await lab_notes.delete(db, note.id)
        logger.info(f"MCP delete_lab_note by {who()}: {note.slug}")
        return f"Deleted '{note.title}' ({note.slug})."

    @server.resource(
        "writing://guide",
        title="Writing guide",
        description="How lab notes on yarikama.com are written",
        mime_type="text/markdown",
    )
    def writing_guide() -> str:
        return GUIDE.read_text()

    @server.resource(
        "notes://{slug}",
        title="Lab note",
        description="A lab note's Markdown body, draft or not",
        mime_type="text/markdown",
    )
    async def note_body(slug: str) -> str:
        async with sessions() as db:
            try:
                return (await lab_notes.get_by_slug(db, slug)).content
            except NotFoundError as error:
                raise ResourceNotFoundError(f"No lab note '{slug}'") from error

    @server.prompt(title="Draft a lab note")
    async def draft_lab_note(
        topic: Annotated[str, Field(description="What the note is about")],
        material: Annotated[
            str, Field(description="Notes, numbers, links to work from")
        ] = "",
    ) -> str:
        async with sessions() as db:
            tags = await lab_notes.tags(db, published_only=False)
        in_use = ", ".join(t["tag"] for t in tags) or "(none yet)"
        return (
            f"{GUIDE.read_text()}\n\n---\n\nTags in use: {in_use}\n\n"
            f"Draft a lab note about: {topic}\n\n"
            + (f"Work from this material:\n\n{material}\n\n" if material else "")
            + "Ask me for anything the material does not say rather than "
            "inventing it. When I'm happy with it, save it with "
            "create_lab_note as a draft."
        )

    @server.prompt(title="Review a lab note")
    async def review_lab_note(
        slug: Annotated[str, Field(description="The note's slug")],
    ) -> str:
        async with sessions() as db:
            try:
                note = await lab_notes.get_by_slug(db, slug)
            except NotFoundError as error:
                raise ToolError(f"No lab note '{slug}'") from error
        return (
            f"{GUIDE.read_text()}\n\n---\n\n"
            f"Review this lab note against the guide above. List the changes "
            f"you would make, most important first, and make them with "
            f"edit_lab_note_content only once I agree.\n\n"
            f"Title: {note.title}\nExcerpt: {note.excerpt}\n"
            f"Tags: {', '.join(note.tags)}\n"
            f"Published: {'yes' if note.published else 'no (draft)'}\n\n"
            f"{note.content}"
        )
