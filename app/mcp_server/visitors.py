"""
The site's visitors, as the admin's Visitors page shows them: Vercel Web
Analytics, read through services/visitors.py. Read-only; the Vercel token
stays on the server.
"""

from typing import Annotated, Any

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from mcp_server.common import READ
from services import visitors

Days = Annotated[
    int,
    Field(
        ge=1,
        le=visitors.MAX_DAYS,
        description="How many days back, today (UTC) included",
    ),
]


def register(server: MCPServer) -> None:
    @server.tool(annotations=READ)
    async def get_visitors(days: Days = 7) -> dict[str, Any]:
        """
        Who visited yarikama.com over the last `days` days: the totals, each
        day, and the top ten pages, referrers (an empty name is a visit with
        none: typed in or a bookmark), countries and device types. Other
        people only: admin pages and browsers signed in to the admin are not
        counted. From Vercel Web Analytics, which splits days at midnight
        UTC, keeps a month and is asked at most every ten minutes per range.
        """
        try:
            report = await visitors.from_settings(days)
        except visitors.NotSetUpError as error:
            raise ToolError(str(error)) from error
        except visitors.VercelUnavailableError as error:
            logger.warning(f"Vercel Web Analytics unavailable: {error}")
            raise ToolError(
                "Vercel Web Analytics is unavailable right now; try again later."
            ) from error
        return visitors.as_json(report)
