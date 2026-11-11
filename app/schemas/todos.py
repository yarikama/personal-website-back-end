from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from schemas.base import BaseSchema

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]

Source = Literal["owner", "daily", "claude"]
# The morning plan's groups: due today, to move on this week, can wait.
Bucket = Literal["today", "week", "later"]


def safe_link(href: str | None) -> str | None:
    """A page of the site, or a web address: never javascript: or the like."""
    if not href or not href.strip():
        return None
    href = href.strip()
    if href.startswith("/") and not href.startswith("//"):
        return href
    if href.startswith(("https://", "http://")):
        return href
    raise ValueError("a link must be a web address or a path on the site")


class TodoCreate(BaseSchema):
    text: Text
    href: str | None = None
    # The owner's today, in their local time.
    day: date

    @field_validator("href")
    @classmethod
    def a_safe_link(cls, href: str | None) -> str | None:
        return safe_link(href)


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
    source: Source
    bucket: Bucket | None
    reason: str | None


class PlanItem(BaseSchema):
    """One item of the morning plan."""

    text: Annotated[
        Text,
        Field(
            description="What to do, as a to-do: a few words at the level of "
            "'Finish COMP 552 HW4' or 'Prepare for the Acme onsite', not steps"
        ),
    ]
    bucket: Bucket
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        Field(description="Why it is on the list, with its deadline if it has one"),
    ]
    href: Annotated[
        str | None, Field(description="A link to open it from, if there is one")
    ] = None

    @field_validator("href")
    @classmethod
    def a_safe_link(cls, href: str | None) -> str | None:
        return safe_link(href)
