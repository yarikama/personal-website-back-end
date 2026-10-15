from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints

from schemas.base import BaseSchema

# Anything but blank: no length limits, so nobody is turned away for a short
# name or a long message. The request body cap (api/body_limit.py, 2 MB)
# bounds them all.
Filled = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContactCreate(BaseSchema):
    name: Filled
    email: EmailStr
    subject: Filled
    message: Filled


class ContactUpdate(BaseSchema):
    read: bool | None = Field(None)
    replied: bool | None = Field(None)


class ContactResponse(BaseSchema):
    id: UUID
    name: str
    email: str
    subject: str
    message: str
    read: bool
    replied: bool
    created_at: datetime
