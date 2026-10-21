"""
The writing MCP server: Claude (Claude Code, claude.ai) reads and writes the
site's content through it, signed in as the owner. Named mcp_server, not
mcp, so as not to hide the SDK's own `mcp` package (PYTHONPATH=app).
"""
