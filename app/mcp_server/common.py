"""What the writing MCP server's tools share: their annotations, the log of
who changed what, and how a missing row or a taken name reaches the model."""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from db.rows import ConflictError, NotFoundError

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
# Visible to the world (publishing) or gone for good (deleting): clients
# should ask first.
OUTWARD = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

Slug = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=255,
        description="Lowercase kebab-case, ASCII: its URL",
    ),
]


def today() -> datetime.date:
    """Today in UTC, like every other date the API keeps."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def who() -> str:
    """The account behind the current request, for the log."""
    token = get_access_token()
    return (token.subject if token else None) or "unknown"


def given(**fields: Any) -> dict[str, Any]:
    """The fields a tool was given: those not left at None."""
    return {name: value for name, value in fields.items() if value is not None}


@contextmanager
def as_tool_errors() -> Iterator[None]:
    """A missing row or a taken slug or name, as an error the model can read
    and correct; anything else stays a crash the client sees no detail of."""
    try:
        yield
    except (NotFoundError, ConflictError) as error:
        raise ToolError(str(error)) from error
