"""
The Learnings page's knowledge graph, whole: the page lays it out itself.
It is written through the MCP server (mcp_server/learnings.py).
"""

from fastapi import APIRouter

from api.dependencies import CurrentAdmin
from db.dependency import DB
from services.content import learnings

router = APIRouter()


@router.get("/admin/learnings/graph")
async def get_learning_graph_admin(_admin: CurrentAdmin, db: DB):
    """Admin endpoint: the whole graph, drafts included."""
    return {"data": await learnings.graph(db, published_only=False)}


@router.get("/learnings/graph")
async def get_learning_graph(db: DB):
    """Public endpoint: the published nodes and the edges between them."""
    return {"data": await learnings.graph(db, published_only=True)}
