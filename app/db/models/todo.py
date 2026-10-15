import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.session import Base


class AdminTodo(Base):
    """
    A to-do on the admin's welcome page. `day` is the day it is for, in the
    owner's local time (the browser says which day it is). Until done it
    carries over to the days after; `done_on` is the day it was done.
    Daily items (`daily_key`) are added once per day; removing one only
    hides it, so it is not added again that day.
    """

    __tablename__ = "admin_todos"
    __table_args__ = (
        Index(
            "uq_admin_todos_daily_day",
            "daily_key",
            "day",
            unique=True,
            postgresql_where=sql("daily_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    text: Mapped[str] = mapped_column(Text)
    href: Mapped[str | None] = mapped_column(Text)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    daily_key: Mapped[str | None] = mapped_column(String(32))
    done_on: Mapped[dt.date | None] = mapped_column(Date)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
