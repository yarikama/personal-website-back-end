import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class AttentionEvent(Base):
    """
    Something the owner's Attention app recorded on one of their devices
    (the iPhone, the iPad), kept so the other devices can catch up: when they
    woke up, when they left home (only the iPhone knows), a payout, or what
    they chose on an intercept screen.

    The app makes the `id`, so sending an event twice stores it once. `seq`
    only grows, so a device asks for what came after the last one it saw.
    `data` is the app's own: the server only stores it.
    """

    __tablename__ = "attention_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    # "wake", "outside", "earning" or "intercept".
    kind: Mapped[str] = mapped_column(String(32))
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    # Which device recorded it, as the app names it (e.g. "iphone", "ipad").
    device: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
