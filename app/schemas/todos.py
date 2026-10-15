from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints, field_validator

from schemas.base import BaseSchema

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class TodoCreate(BaseSchema):
    text: Text
    href: str | None = None
    # The owner's today, in their local time.
    day: date

    @field_validator("href")
    @classmethod
    def a_safe_link(cls, href: str | None) -> str | None:
        """A page of the site, or a web address: never javascript: or the like."""
        if not href or not href.strip():
            return None
        href = href.strip()
        if href.startswith("/") and not href.startswith("//"):
            return href
        if href.startswith(("https://", "http://")):
            return href
        raise ValueError("a link must be a web address or a path on the site")


class TodoUpdate(BaseSchema):
    done: bool
    # The day it is done on, in the owner's local time.
    day: date


class TodoResponse(BaseSchema):
    id: UUID
    text: str
    href: str | None
    day: date
    daily_key: str | None
    done_on: date | None
    created_at: datetime
