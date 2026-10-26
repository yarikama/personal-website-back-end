from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from core.config import DATABASE_URL
from db.session import Base, psycopg_url
from db.models.projects import Project
from db.models.lab_notes import LabNote
from db.models.contact import ContactMessage
# Every model must be imported here, or autogenerate proposes dropping its
# table.
from db.models.category import Category
from db.models.autocomplete import AutocompleteSuggestion
from db.models.ask import AskQuestion
from db.models.admin_seen import AdminSeen
from db.models.todo import AdminTodo
from db.models.admin_setting import AdminSetting
from db.models.mcp_oauth import McpClient, McpRefreshToken
from db.models.learnings import LearningEdge, LearningNode

target_metadata = Base.metadata

# The app's database, through psycopg 3's sync driver. Passed straight to
# SQLAlchemy rather than through alembic.ini, whose interpolation would take a
# % in an escaped password for a reference.
url = psycopg_url(DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
