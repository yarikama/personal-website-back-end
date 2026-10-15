from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base


class AdminSeen(Base):
    """
    When each admin last opened a page that counts what is new since
    (Questions), so the nav can say how many came in meanwhile. Per account,
    in the database, so it holds across browsers and devices.
    """

    __tablename__ = "admin_seen"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    page: Mapped[str] = mapped_column(String(32), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
