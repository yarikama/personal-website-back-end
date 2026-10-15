import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    # Nullable in the database; new rows always get a value.
    read: Mapped[bool | None] = mapped_column(Boolean, default=False)
    replied: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
