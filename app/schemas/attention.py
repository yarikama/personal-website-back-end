from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from schemas.base import BaseSchema

Kind = Literal["wake", "outside", "earning", "intercept"]
Device = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class EventIn(BaseSchema):
    id: UUID
    kind: Kind
    at: datetime
    device: Device
    data: dict[str, Any] = Field(default_factory=dict)


class EventsIn(BaseSchema):
    events: list[EventIn] = Field(max_length=500)


class EventOut(EventIn):
    seq: int
