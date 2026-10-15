"""
Every question asked in the chat, kept in the database for
ASK_QUESTION_RETENTION_DAYS: what visitors ask, longer than Loki's week of
logs, and readable in the admin area. No address or other identifier of
the visitor is stored.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import delete

from core import config
from db.models.ask import AskQuestion
from db.session import SessionLocal


@dataclass
class Entry:
    question: str
    quote: str | None
    page: str | None
    answer: str
    citations: list[dict[str, Any]]
    status: str  # answered | error
    truncated: bool
    output_tokens: int | None
    duration_ms: int
    admin: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def record(entry: Entry) -> None:
    """
    Store a question, and forget those past the retention period. Called
    after the answer is sent; a database problem is logged, never raised,
    since the visitor already has the answer.
    """
    try:
        async with SessionLocal() as db:
            db.add(AskQuestion(**entry.__dict__))
            cutoff = entry.created_at - timedelta(
                days=config.ASK_QUESTION_RETENTION_DAYS
            )
            await db.execute(
                delete(AskQuestion)
                .where(AskQuestion.created_at < cutoff)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
    except Exception as error:
        logger.warning(f"Ask: could not store the question: {error!r}")
