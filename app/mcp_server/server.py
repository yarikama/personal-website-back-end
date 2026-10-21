import logging
from urllib.parse import urlparse

from fastapi import FastAPI
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import Route

from core.config import API_PREFIX, MCP_ISSUER_URL
from db.session import SessionLocal
from mcp_server import lab_notes, projects
from services.mcp_oauth import WritingOAuth

INSTRUCTIONS = (
    "The content of yarikama.com, the owner's personal site: lab notes "
    "(technical essays in Markdown), and the projects on its works page with "
    "their categories. Read the writing://guide resource before writing or "
    "editing a note. Notes and projects are created unpublished; publish or "
    "delete only when the owner asks."
)


def build(
    provider: WritingOAuth, sessions: async_sessionmaker[AsyncSession]
) -> MCPServer:
    issuer = MCP_ISSUER_URL.rstrip("/")
    server = MCPServer(
        name="yarikama-writing",
        title="yarikama.com writing",
        instructions=INSTRUCTIONS,
        auth_server_provider=provider,
        # From strings, which AuthSettings parses keeping an empty path: an
        # AnyHttpUrl made beforehand gains a trailing slash, and clients
        # compare the issuer letter for letter (RFC 8414).
        auth=AuthSettings.model_validate(
            {
                "issuer_url": issuer,
                "resource_server_url": provider.resource,
                "client_registration_options": ClientRegistrationOptions(enabled=True),
                "revocation_options": RevocationOptions(enabled=True),
                # Tokens are only issued for this server
                # (WritingOAuth.authorize); one for any other is refused.
                "validate_token_resource": True,
            }
        ),
    )
    lab_notes.register(server, sessions)
    projects.register(server, sessions)
    return server


def mount(
    app: FastAPI, sessions: async_sessionmaker[AsyncSession] = SessionLocal
) -> None:
    """
    Serves the MCP server at /mcp and its OAuth endpoints (/authorize,
    /token, /register, /revoke and their /.well-known metadata) at the
    root, as the SDK lays them out. Each path is routed to the SDK's app
    unchanged, rather than mounting it at "/", so that every other path
    still gets FastAPI's own 404. core/events.py runs its session manager.

    Stateless and plain JSON: no session to keep between requests, so any
    replica can answer any request, and nothing is streamed through
    Cloudflare.
    """
    issuer = MCP_ISSUER_URL.rstrip("/")
    provider = WritingOAuth(
        redis=lambda: getattr(app.state, "redis", None),
        sessions=sessions,
        consent_url=f"{issuer}{API_PREFIX}/v1/auth/mcp/consent",
        resource=f"{issuer}/mcp",
    )
    server = build(provider, sessions)
    # Without this, the SDK only takes Host: localhost (its guard against DNS
    # rebinding, for servers on the owner's machine).
    security = TransportSecuritySettings(
        allowed_hosts=[urlparse(issuer).netloc, "localhost:*", "127.0.0.1:*"],
        allowed_origins=[issuer, "http://localhost:*", "http://127.0.0.1:*"],
    )
    http = server.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security
    )
    # Stateless, it logs "Terminating session: None" after every request.
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)
    for route in http.routes:
        # The SDK lays out plain routes; anything else would need routing
        # here, and skipping it would lose an endpoint without a word.
        assert isinstance(route, Route), f"unexpected route {route!r}"
        app.router.routes.append(Route(route.path, endpoint=http))
    app.state.mcp = server
    app.state.mcp_oauth = provider
