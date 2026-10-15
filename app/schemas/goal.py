from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints

from schemas.base import BaseSchema


class GoalUpdate(BaseSchema):
    # A few words, shown large; empty clears it.
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class GoalResponse(BaseSchema):
    text: str
    updated_at: datetime | None
