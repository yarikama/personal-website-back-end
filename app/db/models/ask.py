import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class AskQuestion(Base):
    """One question asked in the "ask about my work" chat, and its answer.

    What visitors want to know, which answers cite nothing or get cut off,
    and, with a rating, the evaluation set of real questions. Kept for
    ASK_QUESTION_RETENTION_DAYS; the visitor's address is never stored.
    """

    __tablename__ = "ask_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    # A passage the visitor highlighted, and the page it is on.
    quote: Mapped[str | None] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(String(200))
    # As streamed, so a broken-off answer keeps what was sent before it broke.
    answer: Mapped[str] = mapped_column(Text)
    # [{"id", "kind", "title", "url"}] as sent in the done event. Ids like P3
    # change when content does; the title and url say what was meant.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # answered | error (the answer broke off).
    status: Mapped[str] = mapped_column(String(16))
    # The answer hit ASK_MAX_TOKENS and ends mid-sentence.
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # From the model accepting the question to the end of the answer.
    duration_ms: Mapped[int] = mapped_column(Integer)
    # The owner, logged in, trying the chat; not a visitor.
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # good | bad, set by the owner in the admin area; null until then.
    rating: Mapped[str | None] = mapped_column(String(8), index=True)
