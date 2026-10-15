import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class AutocompleteSuggestion(Base):
    """One suggestion shown in the note editor, and what became of it.

    Written when the editor reports the outcome, so every row was shown.
    (Rows from before 2026-09-29 were written on generation; those with a
    null outcome were never shown.)

    This is the training and evaluation data for the autocomplete model: the
    context it saw, what it proposed, and whether the author took it.
    """

    __tablename__ = "autocomplete_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    model_version: Mapped[str] = mapped_column(String(200))
    # The lab note being edited; null while writing a note not saved yet.
    note_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # The prompt text the model continued (title header plus the text before
    # the cursor, trimmed to the context limit).
    prompt: Mapped[str] = mapped_column(Text)
    # What the model generated, and the part of it that was shown.
    completion: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    # [[token, logprob], ...] for the completion, to revisit the cut-off.
    # Since generation stops at the first unsure token, this ends with that
    # token (or a sentence end), not after 16 tokens.
    tokens: Mapped[list[list[Any]] | None] = mapped_column(JSONB)
    # The confidence cut-off in force when it was shown, to compare settings.
    min_token_prob: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    # accepted | rejected | ignored; null until the editor reports back.
    outcome: Mapped[str | None] = mapped_column(String(16), index=True)
    # How much of the suggestion ended up in the text (all of it on Tab,
    # a prefix when the author typed along with it).
    accepted_chars: Mapped[int | None] = mapped_column(Integer)
    # The same in generated tokens: where the author stopped taking it.
    accepted_tokens: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
