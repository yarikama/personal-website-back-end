"""
The migrations form one line. Two heads (two migrations written on top of
the same one) pass every other test, and only fail in production, where
the init container's `alembic upgrade head` refuses to choose.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

INI = Path(__file__).resolve().parent.parent / "app" / "alembic.ini"


def test_the_migrations_have_one_head():
    heads = ScriptDirectory.from_config(Config(str(INI))).get_heads()
    assert len(heads) == 1, f"several heads: {heads}"
