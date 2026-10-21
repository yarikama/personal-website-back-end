"""Health endpoint for container orchestrators."""

from fastapi import APIRouter

router = APIRouter()


# HEAD too: uptime checks such as the README's status badge send HEAD.
@router.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
async def health() -> dict[str, str]:
    """Report that the process is up and able to serve requests.

    This deliberately does not query the database. Kubernetes calls it every
    few seconds, so a database check would keep the serverless Postgres from
    ever scaling to zero, and a brief database outage would restart every pod.
    """
    return {"status": "ok"}
