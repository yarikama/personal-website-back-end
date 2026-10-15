from core.config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# A pooled connection can be dead by the time a request checks it out (the
# database pod restarted or failed over; originally Neon, which suspends
# after a few idle minutes). pre_ping tests it with SELECT 1 and reconnects
# instead of failing the request; recycle retires connections before they get
# that old.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def async_url(url: str) -> URL:
    """The same database through psycopg 3's asyncio driver. DATABASE_URL
    stays a plain postgresql:// URL (what CloudNativePG's secret holds), which
    the sync engine and Alembic still read with psycopg2."""
    return make_url(url).set(drivername="postgresql+psycopg")


# The routes moving off the sync engine use this one: a query awaits the
# database instead of holding the event loop. Same pool settings.
async_engine = create_async_engine(
    async_url(DATABASE_URL), pool_pre_ping=True, pool_recycle=300
)
# expire_on_commit=False: after a commit, reading an attribute would otherwise
# reload it, a query that cannot run outside an await (MissingGreenlet).
AsyncSessionLocal = async_sessionmaker(
    async_engine, autoflush=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    """The models' base: typed columns (Mapped[...]), so an attribute reads as
    its value's type (str, datetime) rather than Column[...]."""
