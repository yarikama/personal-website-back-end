from typing import Literal
from uuid import UUID

from pydantic import Field

from schemas.base import BaseSchema


class CompletionRequest(BaseSchema):
    # Text before the cursor. Only the tail is used; the limit just rejects
    # absurd payloads.
    prefix: str = Field(..., max_length=200_000)
    title: str = Field("", max_length=300)
    note_id: UUID | None = None


class CompletionResponse(BaseSchema):
    # null when nothing was shown (no confident continuation, or the model is
    # unavailable), so there is nothing to report feedback for.
    id: UUID | None
    suggestion: str


class SuggestionFeedback(BaseSchema):
    outcome: Literal["accepted", "rejected", "ignored"]
    accepted_chars: int | None = Field(None, ge=0)
