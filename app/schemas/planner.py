from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints

from schemas.base import BaseSchema

# A page of Markdown at most; empty clears it.
Page = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


class PlannerUpdate(BaseSchema):
    """What the morning plan works from: the owner's long-term goals, and
    the notes it keeps on the owner between mornings. Each is left as it
    is when not given."""

    goals: Page | None = None
    notes: Page | None = None


class PlannerResponse(BaseSchema):
    goals: str
    goals_updated_at: datetime | None
    notes: str
    notes_updated_at: datetime | None
