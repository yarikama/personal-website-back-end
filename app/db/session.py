from core.config import DATABASE_URL
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


def psycopg_url(url: str) -> URL:
    """The database through psycopg 3, its only driver: asyncio for the app,
    sync for Alembic and the tests' setup. DATABASE_URL stays a plain
    postgresql:// URL, as CloudNativePG's secret holds it."""
    return make_url(url).set(drivername="postgresql+psycopg")


# A pooled connection can be dead by the time a request checks it out (the
# database pod restarted or failed over; originally Neon, which suspends
# after a few idle minutes). pre_ping tests it with SELECT 1 and reconnects
# instead of failing the request; recycle retires connections before they get
# that old.
engine = create_async_engine(
    psycopg_url(DATABASE_URL), pool_pre_ping=True, pool_recycle=300
)
# expire_on_commit=False: after a commit, reading an attribute would otherwise
# reload it, a query that cannot run outside an await (MissingGreenlet).
SessionLocal = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """The models' base: typed columns (Mapped[...]), so an attribute reads as
    its value's type (str, datetime) rather than Column[...]."""
